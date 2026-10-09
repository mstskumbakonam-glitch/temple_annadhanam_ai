"""Daily staff attendance register (manual, supervisor-marked).

Rules: one entry per person per day (unique in the database; re-marking updates
it). No entry = "not marked", which is reported separately and is NEVER counted
as absent. Dates in the future cannot be marked.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, joinedload

from app.models import AnnadhanamHall, Staff, StaffDailyAttendance, Temple
from app.models.enums import DailyAttendanceStatus
from app.schemas.management import (
    AttendanceBulk,
    AttendanceRegister,
    RegisterRow,
    StaffAttendanceSummary,
)
from app.services.exceptions import NotFoundError, ValidationError
from app.services.session_service import local_today
from app.utils.time import utc_now


def _staff_query(temple_code: str | None, hall_code: str | None):
    stmt = (select(Staff).options(joinedload(Staff.temple), joinedload(Staff.hall))
            .where(Staff.active.is_(True)).order_by(Staff.staff_name, Staff.staff_code))
    if temple_code:
        stmt = stmt.where(Staff.temple.has(Temple.temple_code == temple_code))
    if hall_code:
        stmt = stmt.where(Staff.hall.has(AnnadhanamHall.hall_code == hall_code))
    return stmt


def attendance_in_use(session: Session) -> bool:
    return session.scalar(select(StaffDailyAttendance.id).limit(1)) is not None


def summarize(session: Session, day: date, staff_ids: list[int]) -> StaffAttendanceSummary:
    counts: dict[str, int] = {}
    if staff_ids:
        counts = dict(session.execute(
            select(StaffDailyAttendance.status, func.count())
            .where(StaffDailyAttendance.attendance_date == day,
                   StaffDailyAttendance.staff_id.in_(staff_ids))
            .group_by(StaffDailyAttendance.status)).all())
    marked = sum(counts.values())
    return StaffAttendanceSummary(
        in_use=attendance_in_use(session), date=day,
        present=counts.get(DailyAttendanceStatus.PRESENT, 0),
        half_day=counts.get(DailyAttendanceStatus.HALF_DAY, 0),
        absent=counts.get(DailyAttendanceStatus.ABSENT, 0),
        on_leave=counts.get(DailyAttendanceStatus.LEAVE, 0),
        not_marked=max(0, len(staff_ids) - marked),
    )


def register(session: Session, day: date, *, temple_code: str | None = None,
             hall_code: str | None = None) -> AttendanceRegister:
    staff = list(session.scalars(_staff_query(temple_code, hall_code)).unique())
    ids = [s.id for s in staff]
    entries = {e.staff_id: e for e in session.scalars(
        select(StaffDailyAttendance).where(StaffDailyAttendance.attendance_date == day,
                                           StaffDailyAttendance.staff_id.in_(ids or [-1])))}
    rows = []
    for s in staff:
        e = entries.get(s.id)
        rows.append(RegisterRow(
            staff_code=s.staff_code, staff_name=s.staff_name, designation=s.designation, shift=s.shift,
            temple_code=s.temple.temple_code if s.temple else None,
            hall_code=s.hall.hall_code if s.hall else None,
            status=e.status if e else None, check_in_at=e.check_in_at if e else None,
            check_out_at=e.check_out_at if e else None, note=e.note if e else None,
        ))
    return AttendanceRegister(date=day, rows=rows, summary=summarize(session, day, ids))


def mark(session: Session, bulk: AttendanceBulk, tz_name: str) -> int:
    today = local_today(tz_name)
    codes = {e.staff_code for e in bulk.entries}
    staff = {s.staff_code: s for s in session.scalars(select(Staff).where(Staff.staff_code.in_(codes)))}
    missing = codes - set(staff)
    if missing:
        raise NotFoundError(f"Unknown staff: {', '.join(sorted(missing))}.")
    table = StaffDailyAttendance.__table__
    for entry in bulk.entries:
        if entry.attendance_date > today:
            raise ValidationError("Attendance cannot be marked for a future date.")
        values = dict(staff_id=staff[entry.staff_code].id, attendance_date=entry.attendance_date,
                      status=entry.status, check_in_at=entry.check_in_at,
                      check_out_at=entry.check_out_at, note=entry.note, source="MANUAL")
        stmt = pg_insert(table).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["staff_id", "attendance_date"],
            set_={k: stmt.excluded[k] for k in ("status", "check_in_at", "check_out_at", "note", "source")}
            | {"updated_at": utc_now()},
        )
        session.execute(stmt)
    session.commit()
    return len(bulk.entries)
