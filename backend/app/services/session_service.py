"""Annadhanam session scheduling.

Times are entered and shown in SITE_TIMEZONE (Asia/Kolkata) and stored as UTC
instants plus the local date. Overlaps in one hall are rejected twice: first by
a friendly check that names the clashing session, then by the database's
EXCLUDE constraint, which also covers two people saving at the same moment.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models import AnnadhanamHall, AnnadhanamSession, Staff, Temple
from app.models.enums import SessionStatus
from app.schemas.management import SessionCancel, SessionCreate, SessionRead, SessionUpdate
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.pagination import PageResult, paginate
from app.services.temple_service import get_hall, seat_counts_by_hall
from app.utils.time import utc_now

FINAL = {SessionStatus.CANCELLED, SessionStatus.COMPLETED}
ALLOWED_STATUS = {
    SessionStatus.SCHEDULED: {SessionStatus.SCHEDULED, SessionStatus.IN_PROGRESS, SessionStatus.COMPLETED},
    SessionStatus.IN_PROGRESS: {SessionStatus.IN_PROGRESS, SessionStatus.COMPLETED, SessionStatus.SCHEDULED},
    SessionStatus.COMPLETED: {SessionStatus.COMPLETED},
    SessionStatus.CANCELLED: {SessionStatus.CANCELLED},
}


def instants(day: date, start: time, end: time, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    return (datetime.combine(day, start, tzinfo=tz).astimezone(timezone.utc),
            datetime.combine(day, end, tzinfo=tz).astimezone(timezone.utc))


def local_today(tz_name: str, now: datetime | None = None) -> date:
    return (now or utc_now()).astimezone(ZoneInfo(tz_name)).date()


def _query() -> Select:
    return (select(AnnadhanamSession)
            .options(joinedload(AnnadhanamSession.hall).joinedload(AnnadhanamHall.temple),
                     joinedload(AnnadhanamSession.responsible_staff)))


def session_read(s: AnnadhanamSession, capacity: int, tz_name: str,
                 now: datetime | None = None) -> SessionRead:
    now = now or utc_now()
    tz = ZoneInfo(tz_name)
    warning = None
    if s.status not in FINAL:
        if capacity == 0:
            warning = "This hall has no seats in service."
        elif s.expected_devotees > capacity:
            warning = (f"Expected {s.expected_devotees} devotees but the hall seats {capacity}; "
                       f"plan about {-(-s.expected_devotees // capacity)} sittings.")
        elif not s.hall.active:
            warning = "The hall is marked inactive."
    timing = "upcoming" if now < s.starts_at else ("ongoing" if now < s.ends_at else "past")
    return SessionRead(
        id=s.id, name=s.name, meal_type=s.meal_type,
        temple_code=s.hall.temple.temple_code, temple_name=s.hall.temple.name,
        hall_code=s.hall.hall_code, hall_name=s.hall.name,
        session_date=s.session_date,
        start_time=s.starts_at.astimezone(tz).time().replace(tzinfo=None),
        end_time=s.ends_at.astimezone(tz).time().replace(tzinfo=None),
        starts_at=s.starts_at, ends_at=s.ends_at,
        expected_devotees=s.expected_devotees, actual_devotees=s.actual_devotees,
        hall_capacity=capacity, capacity_warning=warning, status=s.status, timing=timing,
        responsible_staff_code=s.responsible_staff.staff_code if s.responsible_staff else None,
        responsible_staff_name=s.responsible_staff.staff_name if s.responsible_staff else None,
        notes=s.notes, cancel_reason=s.cancel_reason, is_demo=s.is_demo,
    )


def reads(session: Session, rows: list[AnnadhanamSession], tz_name: str) -> list[SessionRead]:
    counts = seat_counts_by_hall(session, {r.hall.hall_code for r in rows})
    now = utc_now()
    return [session_read(r, counts[r.hall.hall_code].capacity if r.hall.hall_code in counts else 0,
                         tz_name, now) for r in rows]


def get(session: Session, session_id: int) -> AnnadhanamSession:
    row = session.scalar(_query().where(AnnadhanamSession.id == session_id))
    if row is None:
        raise NotFoundError(f"Session {session_id} not found.")
    return row


def list_sessions(session: Session, *, page: int, page_size: int, start_date: date | None = None,
                  end_date: date | None = None, temple_code: str | None = None,
                  hall_code: str | None = None, status: SessionStatus | None = None,
                  timing: str | None = None) -> PageResult:
    stmt = (_query().join(AnnadhanamHall, AnnadhanamSession.hall_id == AnnadhanamHall.id)
            .join(Temple, AnnadhanamHall.temple_id == Temple.id)
            .order_by(AnnadhanamSession.starts_at, AnnadhanamHall.name))
    if start_date:
        stmt = stmt.where(AnnadhanamSession.session_date >= start_date)
    if end_date:
        stmt = stmt.where(AnnadhanamSession.session_date <= end_date)
    if temple_code:
        stmt = stmt.where(Temple.temple_code == temple_code)
    if hall_code:
        stmt = stmt.where(AnnadhanamHall.hall_code == hall_code)
    if status:
        stmt = stmt.where(AnnadhanamSession.status == status)
    now = utc_now()
    if timing == "upcoming":
        stmt = stmt.where(AnnadhanamSession.starts_at > now)
    elif timing == "past":
        stmt = stmt.where(AnnadhanamSession.ends_at <= now)
    return paginate(session, stmt, page, page_size)


def _staff_id(session: Session, code: str | None) -> int | None:
    if code is None:
        return None
    staff = session.scalar(select(Staff).where(Staff.staff_code == code))
    if staff is None:
        raise NotFoundError(f"Staff '{code}' not found.")
    if not staff.active:
        raise ValidationError(f"Staff '{code}' is not active.")
    return staff.id


def _check_conflict(session: Session, hall_id: int, starts: datetime, ends: datetime,
                    exclude_id: int | None) -> None:
    stmt = select(AnnadhanamSession).where(
        AnnadhanamSession.hall_id == hall_id,
        AnnadhanamSession.status != SessionStatus.CANCELLED,
        and_(AnnadhanamSession.starts_at < ends, AnnadhanamSession.ends_at > starts),
    )
    if exclude_id is not None:
        stmt = stmt.where(AnnadhanamSession.id != exclude_id)
    clash = session.scalars(stmt.limit(1)).first()
    if clash is not None:
        raise ConflictError(
            f"The hall already has '{clash.name}' (session {clash.id}) at an overlapping time.")


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23P01":   # exclusion_violation
            raise ConflictError("Another session was saved for this hall at an overlapping "
                                "time just now. Refresh and choose another slot.") from exc
        raise ConflictError("The session could not be saved.") from exc


def create(session: Session, payload: SessionCreate, tz_name: str) -> SessionRead:
    hall = get_hall(session, payload.hall_code)
    if not hall.active:
        raise ValidationError(f"Hall '{payload.hall_code}' is inactive.")
    starts, ends = instants(payload.session_date, payload.start_time, payload.end_time, tz_name)
    _check_conflict(session, hall.id, starts, ends, None)
    row = AnnadhanamSession(
        hall_id=hall.id, name=payload.name, meal_type=payload.meal_type,
        session_date=payload.session_date, starts_at=starts, ends_at=ends,
        expected_devotees=payload.expected_devotees,
        responsible_staff_id=_staff_id(session, payload.responsible_staff_code),
        notes=payload.notes,
    )
    session.add(row)
    _commit(session)
    return reads(session, [get(session, row.id)], tz_name)[0]


def update(session: Session, session_id: int, payload: SessionUpdate, tz_name: str) -> SessionRead:
    row = get(session, session_id)
    changes = payload.model_dump(exclude_unset=True)
    if row.status == SessionStatus.CANCELLED:
        raise ConflictError("A cancelled session cannot be edited.")
    if row.status == SessionStatus.COMPLETED and set(changes) - {"actual_devotees", "notes"}:
        raise ConflictError("A completed session only accepts actual_devotees and notes.")

    tz = ZoneInfo(tz_name)
    def pick(key, current):
        return changes[key] if changes.get(key) is not None else current

    day = pick("session_date", row.session_date)
    start = pick("start_time", row.starts_at.astimezone(tz).time().replace(tzinfo=None))
    end = pick("end_time", row.ends_at.astimezone(tz).time().replace(tzinfo=None))
    if end <= start:
        raise ValidationError("end_time must be after start_time.")
    hall = get_hall(session, changes["hall_code"]) if changes.get("hall_code") else row.hall
    starts, ends = instants(day, start, end, tz_name)
    if (hall.id, starts, ends) != (row.hall_id, row.starts_at, row.ends_at):
        _check_conflict(session, hall.id, starts, ends, row.id)
        row.hall_id, row.session_date, row.starts_at, row.ends_at = hall.id, day, starts, ends

    if "status" in changes and changes["status"] is not None:
        target = SessionStatus(changes["status"])
        if target not in ALLOWED_STATUS[SessionStatus(row.status)]:
            raise ConflictError(f"Cannot change a {row.status} session to {target}.")
        if target == SessionStatus.COMPLETED and utc_now() < row.starts_at:
            raise ValidationError("A session cannot be completed before it starts.")
        row.status = target
    if "responsible_staff_code" in changes:
        row.responsible_staff_id = _staff_id(session, changes["responsible_staff_code"])
    for field in ("name", "meal_type", "expected_devotees", "actual_devotees", "notes"):
        if field in changes and (changes[field] is not None or field in ("actual_devotees", "notes")):
            setattr(row, field, changes[field])
    _commit(session)
    session.expire_all()
    return reads(session, [get(session, session_id)], tz_name)[0]


def cancel(session: Session, session_id: int, payload: SessionCancel, tz_name: str) -> SessionRead:
    row = get(session, session_id)
    if row.status in FINAL:
        raise ConflictError(f"Session {session_id} is already {row.status.lower()}.")
    row.status = SessionStatus.CANCELLED
    row.cancel_reason = payload.reason
    _commit(session)
    return reads(session, [get(session, session_id)], tz_name)[0]


def summary(session: Session, tz_name: str, hall_ids: list[int] | None = None) -> dict:
    today = local_today(tz_name)
    base = select(AnnadhanamSession.status, func.count()).where(AnnadhanamSession.session_date == today)
    if hall_ids is not None:
        base = base.where(AnnadhanamSession.hall_id.in_(hall_ids or [-1]))
    counts = dict(session.execute(base.group_by(AnnadhanamSession.status)).all())
    upcoming_q = select(func.count()).select_from(AnnadhanamSession).where(
        AnnadhanamSession.status == SessionStatus.SCHEDULED, AnnadhanamSession.starts_at > utc_now())
    completed_q = select(func.count()).select_from(AnnadhanamSession).where(
        AnnadhanamSession.status == SessionStatus.COMPLETED)
    if hall_ids is not None:
        upcoming_q = upcoming_q.where(AnnadhanamSession.hall_id.in_(hall_ids or [-1]))
        completed_q = completed_q.where(AnnadhanamSession.hall_id.in_(hall_ids or [-1]))
    return {
        "today_total": sum(v for k, v in counts.items() if k != SessionStatus.CANCELLED),
        "today_scheduled": counts.get(SessionStatus.SCHEDULED, 0),
        "today_in_progress": counts.get(SessionStatus.IN_PROGRESS, 0),
        "today_completed": counts.get(SessionStatus.COMPLETED, 0),
        "today_cancelled": counts.get(SessionStatus.CANCELLED, 0),
        "upcoming": session.scalar(upcoming_q) or 0,
        "completed_total": session.scalar(completed_q) or 0,
        "today": today,
    }
