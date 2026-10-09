"""Reports built only from stored rows. Each returns (columns, rows) so the API
can serve the same data as JSON or CSV."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import SeatStatusEvent, Staff, StaffDailyAttendance, Temple
from app.models.enums import SeatStatus
from app.services import session_service
from app.services.exceptions import ValidationError
from app.services.temple_service import seat_counts_by_hall
from app.utils.time import utc_now

Report = tuple[list[str], list[dict]]
MAX_DAYS = 366


def _range(start: date, end: date) -> None:
    if end < start:
        raise ValidationError("end_date must not be before start_date.")
    if (end - start).days >= MAX_DAYS:
        raise ValidationError(f"Date range is limited to {MAX_DAYS} days.")


def _temples(session: Session, temple_code: str | None) -> list[Temple]:
    stmt = select(Temple).options(selectinload(Temple.halls)).order_by(Temple.name)
    if temple_code:
        stmt = stmt.where(Temple.temple_code == temple_code)
    return list(session.scalars(stmt))


def temple_capacity(session: Session, temple_code: str | None = None) -> Report:
    temples = _temples(session, temple_code)
    counts = seat_counts_by_hall(session, [h.hall_code for t in temples for h in t.halls])
    cols = ["temple_code", "temple_name", "district", "temple_active", "hall_code", "hall_name",
            "hall_active", "installed_seats", "seat_capacity", "out_of_service"]
    rows = []
    for t in temples:
        if not t.halls:
            rows.append(dict(temple_code=t.temple_code, temple_name=t.name, district=t.district,
                             temple_active=t.active, hall_code=None, hall_name=None, hall_active=None,
                             installed_seats=0, seat_capacity=0, out_of_service=0))
        for h in t.halls:
            c = counts.get(h.hall_code)
            rows.append(dict(temple_code=t.temple_code, temple_name=t.name, district=t.district,
                             temple_active=t.active, hall_code=h.hall_code, hall_name=h.name,
                             hall_active=h.active, installed_seats=c.installed if c else 0,
                             seat_capacity=c.capacity if c else 0,
                             out_of_service=c.out_of_service if c else 0))
    return cols, rows


def hall_occupancy(session: Session, temple_code: str | None = None) -> Report:
    temples = _temples(session, temple_code)
    counts = seat_counts_by_hall(session, [h.hall_code for t in temples for h in t.halls])
    cols = ["temple_code", "hall_code", "hall_name", "hall_active", "capacity", "occupied", "reserved",
            "available", "out_of_service", "occupancy_percentage", "as_of"]
    now = utc_now().isoformat()
    rows = []
    for t in temples:
        for h in t.halls:
            c = counts.get(h.hall_code)
            rows.append(dict(temple_code=t.temple_code, hall_code=h.hall_code, hall_name=h.name,
                             hall_active=h.active, capacity=c.capacity if c else 0,
                             occupied=c.occupied if c else 0, reserved=c.reserved if c else 0,
                             available=c.available if c else 0,
                             out_of_service=c.out_of_service if c else 0,
                             occupancy_percentage=c.occupancy_percentage if c else 0.0, as_of=now))
    return cols, rows


def sessions(session: Session, start: date, end: date, tz_name: str, *, temple_code: str | None = None,
             hall_code: str | None = None, actual_recorded_only: bool = False) -> Report:
    _range(start, end)
    result = session_service.list_sessions(session, page=1, page_size=5000, start_date=start,
                                           end_date=end, temple_code=temple_code, hall_code=hall_code)
    items = session_service.reads(session, result.items, tz_name)
    cols = ["session_id", "date", "start_time", "end_time", "temple_code", "hall_code", "session_name",
            "meal_type", "status", "expected_devotees", "actual_devotees", "difference",
            "hall_capacity", "responsible_staff"]
    rows = []
    for s in items:
        if actual_recorded_only and s.actual_devotees is None:
            continue
        rows.append(dict(
            session_id=s.id, date=s.session_date.isoformat(), start_time=s.start_time.strftime("%H:%M"),
            end_time=s.end_time.strftime("%H:%M"), temple_code=s.temple_code, hall_code=s.hall_code,
            session_name=s.name, meal_type=s.meal_type, status=s.status,
            expected_devotees=s.expected_devotees, actual_devotees=s.actual_devotees,
            difference=(s.actual_devotees - s.expected_devotees) if s.actual_devotees is not None else None,
            hall_capacity=s.hall_capacity, responsible_staff=s.responsible_staff_name))
    return cols, rows


def staff_attendance(session: Session, start: date, end: date, tz_name: str,
                     temple_code: str | None = None) -> Report:
    _range(start, end)
    last = min(end, session_service.local_today(tz_name))
    days = max(0, (last - start).days + 1)
    stmt = select(Staff).where(Staff.active.is_(True)).order_by(Staff.staff_name)
    if temple_code:
        stmt = stmt.where(Staff.temple.has(Temple.temple_code == temple_code))
    staff = list(session.scalars(stmt))
    counts: dict[int, dict[str, int]] = {}
    for staff_id, status, n in session.execute(
            select(StaffDailyAttendance.staff_id, StaffDailyAttendance.status, func.count())
            .where(StaffDailyAttendance.attendance_date.between(start, end),
                   StaffDailyAttendance.staff_id.in_([s.id for s in staff] or [-1]))
            .group_by(StaffDailyAttendance.staff_id, StaffDailyAttendance.status)):
        counts.setdefault(staff_id, {})[status] = n
    cols = ["staff_code", "staff_name", "designation", "present", "half_day", "absent", "leave",
            "not_marked", "days_in_range"]
    rows = []
    for s in staff:
        c = counts.get(s.id, {})
        marked = sum(c.values())
        rows.append(dict(staff_code=s.staff_code, staff_name=s.staff_name, designation=s.designation,
                         present=c.get("PRESENT", 0), half_day=c.get("HALF_DAY", 0),
                         absent=c.get("ABSENT", 0), leave=c.get("LEAVE", 0),
                         not_marked=max(0, days - marked), days_in_range=days))
    return cols, rows


def seat_occupancy_history(session: Session, start: date, end: date, tz_name: str,
                           hall_code: str | None = None) -> Report:
    _range(start, end)
    day = func.date(func.timezone(tz_name, SeatStatusEvent.changed_at)).label("day")
    began = func.count().filter(SeatStatusEvent.to_status == SeatStatus.OCCUPIED)
    ended_n = func.count().filter(SeatStatusEvent.from_status == SeatStatus.OCCUPIED)
    minutes = func.coalesce(func.sum(SeatStatusEvent.previous_duration_seconds)
                            .filter(SeatStatusEvent.from_status == SeatStatus.OCCUPIED), 0) / 60.0
    stmt = (select(day, SeatStatusEvent.hall_id, began, ended_n, minutes)
            .where(func.date(func.timezone(tz_name, SeatStatusEvent.changed_at)).between(start, end))
            .group_by(day, SeatStatusEvent.hall_id).order_by(day, SeatStatusEvent.hall_id))
    if hall_code:
        stmt = stmt.where(SeatStatusEvent.hall_id == hall_code)
    cols = ["date", "hall_code", "occupations_started", "occupations_ended",
            "occupied_seat_minutes", "average_minutes_per_occupation"]
    rows = [dict(date=d.isoformat(), hall_code=h, occupations_started=b, occupations_ended=e,
                 occupied_seat_minutes=round(float(m), 1),
                 average_minutes_per_occupation=round(float(m) / e, 1) if e else None)
            for d, h, b, e, m in session.execute(stmt)]
    return cols, rows


def default_range(tz_name: str, days: int = 7) -> tuple[date, date]:
    today = session_service.local_today(tz_name)
    return today - timedelta(days=days - 1), today

