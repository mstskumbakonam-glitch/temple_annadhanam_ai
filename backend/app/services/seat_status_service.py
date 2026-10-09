"""Confirmed seat status: listing, safe status changes, layout generation, history.

Concurrency: a status change locks the seat row (SELECT ... FOR UPDATE) for the
duration of its transaction and validates the transition against the status it
finds. Two operators pressing "occupy" on the same seat at the same moment are
therefore serialised: the first succeeds, the second sees OCCUPIED and gets 409.
Clients may also send `expected_version`; a mismatch means someone else changed
the seat since the client last looked, and is rejected rather than overwritten.

AI detections never call this module directly. A person detected on a seat is
an AI observation (seat_occupancy / AI views); it becomes a confirmed status
only when an operator confirms it (source = AI_CONFIRMED).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AnnadhanamSession, Seat, SeatStatusEvent
from app.models.enums import SeatStatus, SessionStatus
from app.schemas.management import (
    HallSeatRead,
    SeatGenerate,
    SeatGenerateResult,
    SeatStatusChange,
    SeatStatusEventRead,
)
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.pagination import PageResult
from app.services.temple_service import get_hall
from app.utils.time import utc_now

A, O, R, X = SeatStatus.AVAILABLE, SeatStatus.OCCUPIED, SeatStatus.RESERVED, SeatStatus.OUT_OF_SERVICE

# from -> allowed targets. An occupied seat must be released before it can be
# taken out of service, so nobody is silently "removed" from a seat.
TRANSITIONS: dict[SeatStatus, set[SeatStatus]] = {
    A: {O, R, X},
    R: {O, A, X},
    O: {A},
    X: {A},
}


def seat_read(seat: Seat) -> HallSeatRead:
    return HallSeatRead(
        seat_id=seat.seat_id, hall_id=seat.hall_id, seat_label=seat.seat_label,
        row_number=seat.row_number, column_number=seat.column_number, enabled=seat.enabled,
        status=seat.status, status_source=seat.status_source, status_since=seat.status_since,
        occupied_since=seat.status_since if seat.status == O else None,
        reservation_ref=seat.reservation_ref, reserved_session_id=seat.reserved_session_id,
        status_version=seat.status_version, updated_at=seat.updated_at,
    )


def list_hall_seats(session: Session, hall_code: str, *, status: SeatStatus | None = None,
                    include_disabled: bool = False) -> list[HallSeatRead]:
    get_hall(session, hall_code)
    stmt = select(Seat).where(Seat.hall_id == hall_code)
    if not include_disabled:
        stmt = stmt.where(Seat.enabled.is_(True))
    if status is not None:
        stmt = stmt.where(Seat.status == status)
    stmt = stmt.order_by(Seat.row_number.nulls_last(), Seat.column_number.nulls_last(), Seat.seat_id)
    return [seat_read(s) for s in session.scalars(stmt)]


def change_status(session: Session, hall_code: str, seat_code: str, change: SeatStatusChange,
                  actor_role: str | None) -> HallSeatRead:
    hall = get_hall(session, hall_code)
    if not hall.active:
        raise ConflictError(f"Hall '{hall_code}' is inactive; seat status cannot be changed.")

    seat = session.scalar(
        select(Seat).where(Seat.hall_id == hall_code, Seat.seat_id == seat_code)
        .with_for_update()
    )
    if seat is None:
        raise NotFoundError(f"Seat '{seat_code}' not found in hall '{hall_code}'.")
    if not seat.enabled:
        raise ConflictError(f"Seat '{seat_code}' is disabled.")
    if change.expected_version is not None and change.expected_version != seat.status_version:
        raise ConflictError(
            f"Seat '{seat_code}' was changed by someone else (now {seat.status}). Refresh and retry.")

    current, target = SeatStatus(seat.status), change.status
    if current == target:
        raise ConflictError(f"Seat '{seat_code}' is already {current.value.replace('_', ' ').lower()}.")
    if target not in TRANSITIONS[current]:
        raise ConflictError(
            f"Seat '{seat_code}' cannot go from {current.value} to {target.value}"
            + (" (release it first)." if current == O else "."))

    if change.session_id is not None:
        booking = session.get(AnnadhanamSession, change.session_id)
        if booking is None:
            raise NotFoundError(f"Session {change.session_id} not found.")
        if booking.hall_id != hall.id:
            raise ValidationError("The session is in a different hall.")
        if booking.status in (SessionStatus.CANCELLED, SessionStatus.COMPLETED):
            raise ValidationError(f"Cannot reserve for a {booking.status.lower()} session.")

    now = utc_now()
    duration = max(0, int((now - seat.status_since).total_seconds())) if seat.status_since else None
    session.add(SeatStatusEvent(
        seat_id=seat.id, hall_id=seat.hall_id, from_status=current, to_status=target,
        changed_at=now, source=change.source, actor_role=actor_role,
        reservation_ref=change.reservation_ref, session_id=change.session_id,
        previous_duration_seconds=duration, note=change.note,
    ))
    seat.status = target
    seat.status_source = change.source
    seat.status_since = now
    seat.reservation_ref = change.reservation_ref if target == R else None
    seat.reserved_session_id = change.session_id if target == R else None
    seat.status_version = seat.status_version + 1
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(f"Seat '{seat_code}' could not be updated.") from exc
    session.refresh(seat)
    return seat_read(seat)


def generate_layout(session: Session, hall_code: str, spec: SeatGenerate) -> SeatGenerateResult:
    """Create seats <prefix><NNN> in a rows x columns grid; existing codes are kept."""
    get_hall(session, hall_code)
    existing = set(session.scalars(select(Seat.seat_id).where(Seat.hall_id == hall_code)))
    width = max(3, len(str(spec.rows * spec.columns)))
    created = skipped = 0
    number = 0
    for row in range(1, spec.rows + 1):
        for column in range(1, spec.columns + 1):
            number += 1
            code = f"{spec.prefix}{number:0{width}d}"
            if code in existing:
                skipped += 1
                continue
            session.add(Seat(seat_id=code, hall_id=hall_code, seat_label=f"Row {row} · Seat {column}",
                             row_number=row, column_number=column))
            created += 1
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("Seats were added concurrently; please retry.") from exc
    return SeatGenerateResult(created=created, skipped_existing=skipped, hall_id=hall_code)


def history(session: Session, *, page: int, page_size: int, hall_code: str | None = None,
            seat_code: str | None = None, start=None, end=None) -> tuple[list[SeatStatusEventRead], PageResult]:
    stmt = (select(SeatStatusEvent, Seat.seat_id)
            .join(Seat, Seat.id == SeatStatusEvent.seat_id)
            .order_by(SeatStatusEvent.changed_at.desc(), SeatStatusEvent.id.desc()))
    if hall_code:
        stmt = stmt.where(SeatStatusEvent.hall_id == hall_code)
    if seat_code:
        stmt = stmt.where(Seat.seat_id == seat_code)
    if start is not None:
        stmt = stmt.where(SeatStatusEvent.changed_at >= start)
    if end is not None:
        stmt = stmt.where(SeatStatusEvent.changed_at < end)
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = session.execute(stmt.limit(page_size).offset((page - 1) * page_size)).all()
    items = [SeatStatusEventRead(
        seat_id=code, hall_id=ev.hall_id, from_status=ev.from_status, to_status=ev.to_status,
        changed_at=ev.changed_at, source=ev.source, actor_role=ev.actor_role,
        reservation_ref=ev.reservation_ref, session_id=ev.session_id,
        previous_duration_seconds=ev.previous_duration_seconds, note=ev.note) for ev, code in rows]
    return items, PageResult(items=items, page=page, page_size=page_size, total=total)
