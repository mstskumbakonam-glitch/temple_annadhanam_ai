"""Seat, seat status and seat history logic."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, joinedload

from app.models import Camera, Seat, SeatOccupancy, Staff, Visitor
from app.models.enums import OccupancyStatus
from app.schemas.seat import SeatCreate, SeatStatusRead, SeatUpdate
from app.services.exceptions import ConflictError, NotFoundError
from app.services.pagination import PageResult, paginate
from app.utils.time import utc_now

DEFAULT_HALL = "MAIN"


def _base_query() -> Select:
    return (
        select(Seat)
        .options(joinedload(Seat.camera))
        .order_by(Seat.hall_id, Seat.seat_id)
    )


def _resolve_camera_id(session: Session, camera_code: str | None) -> int | None:
    """Translate a camera display code into its primary key."""
    if camera_code is None:
        return None
    camera = session.scalar(select(Camera).where(Camera.camera_id == camera_code))
    if camera is None:
        raise NotFoundError(f"Camera '{camera_code}' not found.")
    return camera.id


def get_by_code(session: Session, seat_id: str, hall_id: str = DEFAULT_HALL) -> Seat:
    seat = session.scalar(
        _base_query().where(Seat.seat_id == seat_id, Seat.hall_id == hall_id)
    )
    if seat is None:
        raise NotFoundError(f"Seat '{seat_id}' not found in hall '{hall_id}'.")
    return seat


def list_seats(
    session: Session,
    *,
    page: int,
    page_size: int,
    hall_id: str | None = None,
    camera_id: str | None = None,
    enabled: bool | None = None,
) -> PageResult:
    statement = _base_query()
    if hall_id is not None:
        statement = statement.where(Seat.hall_id == hall_id)
    if camera_id is not None:
        statement = statement.join(Camera, Seat.camera_id == Camera.id).where(
            Camera.camera_id == camera_id
        )
    if enabled is not None:
        statement = statement.where(Seat.enabled.is_(enabled))
    return paginate(session, statement, page, page_size)


def _require_hall(session: Session, hall_code: str) -> None:
    from app.models import AnnadhanamHall

    if session.scalar(select(AnnadhanamHall.id).where(AnnadhanamHall.hall_code == hall_code)) is None:
        raise NotFoundError(f"Hall '{hall_code}' not found. Create the hall first.")


def create_seat(session: Session, payload: SeatCreate) -> Seat:
    _require_hall(session, payload.hall_id)
    seat = Seat(
        seat_id=payload.seat_id,
        hall_id=payload.hall_id,
        seat_label=payload.seat_label,
        row_number=payload.row_number,
        column_number=payload.column_number,
        camera_id=_resolve_camera_id(session, payload.camera_id),
        polygon_points=(
            [point.model_dump() for point in payload.polygon_points]
            if payload.polygon_points is not None
            else None
        ),
        enabled=payload.enabled,
    )
    session.add(seat)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            f"Seat '{payload.seat_id}' already exists in hall '{payload.hall_id}'."
        ) from exc
    session.refresh(seat)
    return seat


def update_seat(
    session: Session, seat_id: str, payload: SeatUpdate, hall_id: str = DEFAULT_HALL
) -> Seat:
    seat = get_by_code(session, seat_id, hall_id)
    changes = payload.model_dump(exclude_unset=True)

    if "camera_id" in changes:
        seat.camera_id = _resolve_camera_id(session, changes.pop("camera_id"))
    if "polygon_points" in changes:
        points = changes.pop("polygon_points")
        seat.polygon_points = points if points is None else list(points)

    for field, value in changes.items():
        setattr(seat, field, value)

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(f"Seat '{seat_id}' could not be updated.") from exc
    session.refresh(seat)
    return seat


def delete_seat(session: Session, seat_id: str, hall_id: str = DEFAULT_HALL) -> None:
    seat = get_by_code(session, seat_id, hall_id)
    session.delete(seat)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            f"Seat '{seat_id}' has occupancy history and cannot be deleted. "
            "Set enabled=false instead."
        ) from exc


# ---------------------------------------------------------------- seat status
def _active_occupancy_query() -> Select:
    """Seats left-joined to their one open occupancy row, if any.

    A partial unique index guarantees at most one OCCUPIED row per seat, so this
    join cannot multiply rows.
    """
    occupancy = aliased(SeatOccupancy)
    seat_camera = aliased(Camera)
    return (
        select(Seat, occupancy, Visitor.visitor_code, Staff.staff_code, seat_camera.camera_id)
        .select_from(Seat)
        .outerjoin(
            occupancy,
            (occupancy.seat_id == Seat.id)
            & (occupancy.status == OccupancyStatus.OCCUPIED),
        )
        .outerjoin(Visitor, occupancy.visitor_id == Visitor.id)
        .outerjoin(Staff, occupancy.staff_id == Staff.id)
        .outerjoin(seat_camera, Seat.camera_id == seat_camera.id)
        .order_by(Seat.hall_id, Seat.seat_id)
    )


def list_seat_status(
    session: Session, *, hall_id: str | None = None, camera_id: str | None = None
) -> list[SeatStatusRead]:
    """Live OCCUPIED/EMPTY state for every seat."""
    statement = _active_occupancy_query().where(Seat.enabled.is_(True))
    if hall_id is not None:
        statement = statement.where(Seat.hall_id == hall_id)
    if camera_id is not None:
        resolved = _resolve_camera_id(session, camera_id)
        statement = statement.where(Seat.camera_id == resolved)

    now = utc_now()
    results: list[SeatStatusRead] = []
    for seat, occupancy, visitor_code, staff_code, seat_camera_code in session.execute(
        statement
    ):
        if occupancy is None:
            results.append(
                SeatStatusRead(
                    seat_id=seat.seat_id,
                    hall_id=seat.hall_id,
                    camera_id=seat_camera_code,
                    seat_label=seat.seat_label,
                    status="EMPTY",
                )
            )
            continue

        results.append(
            SeatStatusRead(
                seat_id=seat.seat_id,
                hall_id=seat.hall_id,
                camera_id=seat_camera_code,
                seat_label=seat.seat_label,
                status="OCCUPIED",
                person_type=occupancy.person_type,
                visitor_code=visitor_code,
                staff_code=staff_code,
                occupied_at=occupancy.occupied_at,
                duration_seconds=int((now - occupancy.occupied_at).total_seconds()),
            )
        )
    return results


# --------------------------------------------------------------- seat history
def list_seat_history(
    session: Session,
    *,
    page: int,
    page_size: int,
    seat_id: str | None = None,
    hall_id: str | None = None,
    camera_id: str | None = None,
    person_type: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> PageResult:
    statement = (
        select(SeatOccupancy)
        .options(
            joinedload(SeatOccupancy.seat),
            joinedload(SeatOccupancy.visitor),
            joinedload(SeatOccupancy.staff),
            joinedload(SeatOccupancy.camera),
        )
        .join(Seat, SeatOccupancy.seat_id == Seat.id)
        .order_by(SeatOccupancy.occupied_at.desc(), SeatOccupancy.id.desc())
    )
    if seat_id is not None:
        statement = statement.where(Seat.seat_id == seat_id)
    if hall_id is not None:
        statement = statement.where(Seat.hall_id == hall_id)
    if camera_id is not None:
        statement = statement.where(
            SeatOccupancy.camera_id == _resolve_camera_id(session, camera_id)
        )
    if person_type is not None:
        statement = statement.where(SeatOccupancy.person_type == person_type)
    if start_time is not None:
        statement = statement.where(SeatOccupancy.occupied_at >= start_time)
    if end_time is not None:
        statement = statement.where(SeatOccupancy.occupied_at < end_time)
    return paginate(session, statement, page, page_size)


# ------------------------------------------------------------ occupancy counts
def occupancy_counts(session: Session, hall_id: str | None = None) -> tuple[int, int]:
    """(seat_capacity, occupied_seats) from the CONFIRMED seat status.

    Capacity = enabled seats not out of service; occupied = status OCCUPIED.
    AI observations (seat_occupancy rows) are not confirmations and are not counted.
    """
    from app.services.temple_service import add_counts, seat_counts_by_hall

    counts = seat_counts_by_hall(session, [hall_id] if hall_id is not None else None)
    total = add_counts(counts.values())
    return total.capacity, total.occupied


def occupancy_percentage(total: int, occupied: int) -> float:
    """occupied / total * 100, returning 0.0 when the hall has no seats."""
    if total <= 0:
        return 0.0
    return round(occupied / total * 100, 2)
