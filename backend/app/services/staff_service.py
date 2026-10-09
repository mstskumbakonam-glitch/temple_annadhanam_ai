"""Staff and attendance business logic."""

from __future__ import annotations

from datetime import datetime

from datetime import date

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models import AnnadhanamHall, Camera, Staff, StaffAttendance, StaffDailyAttendance, Temple
from app.schemas.staff import StaffCreate, StaffRead, StaffUpdate
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.pagination import PageResult, paginate


def _base_query() -> Select:
    return (select(Staff)
            .options(joinedload(Staff.temple), joinedload(Staff.hall))
            .order_by(Staff.staff_code))


def get_by_code(session: Session, staff_code: str) -> Staff:
    staff = session.scalar(_base_query().where(Staff.staff_code == staff_code))
    if staff is None:
        raise NotFoundError(f"Staff '{staff_code}' not found.")
    return staff


def to_read(staff: Staff, today_status: str | None = None, *, show_phone: bool) -> StaffRead:
    return StaffRead(
        staff_code=staff.staff_code, staff_name=staff.staff_name, employee_code=staff.employee_code,
        department=staff.department, designation=staff.designation, shift=staff.shift,
        phone=staff.phone if show_phone else None, active=staff.active,
        temple_code=staff.temple.temple_code if staff.temple else None,
        hall_code=staff.hall.hall_code if staff.hall else None,
        is_demo=staff.is_demo, today_attendance=today_status,
        created_at=staff.created_at, updated_at=staff.updated_at,
    )


def today_statuses(session: Session, staff_ids: list[int], day: date) -> dict[int, str]:
    if not staff_ids:
        return {}
    return dict(session.execute(
        select(StaffDailyAttendance.staff_id, StaffDailyAttendance.status)
        .where(StaffDailyAttendance.staff_id.in_(staff_ids),
               StaffDailyAttendance.attendance_date == day)).all())


def list_staff(
    session: Session,
    *,
    page: int,
    page_size: int,
    active: bool | None = None,
    department: str | None = None,
    search: str | None = None,
    temple_code: str | None = None,
    hall_code: str | None = None,
    shift: str | None = None,
) -> PageResult:
    statement = _base_query()
    if active is not None:
        statement = statement.where(Staff.active.is_(active))
    if department is not None:
        statement = statement.where(Staff.department == department)
    if search:
        like = f"%{search.strip().lower()}%"
        statement = statement.where(or_(func.lower(Staff.staff_name).like(like),
                                        func.lower(Staff.staff_code).like(like),
                                        func.lower(Staff.designation).like(like)))
    if temple_code:
        statement = statement.where(Staff.temple.has(Temple.temple_code == temple_code))
    if hall_code:
        statement = statement.where(Staff.hall.has(AnnadhanamHall.hall_code == hall_code))
    if shift:
        statement = statement.where(Staff.shift == shift)
    return paginate(session, statement, page, page_size)


def _assignment(session: Session, temple_code: str | None, hall_code: str | None,
                current_temple_id: int | None = None) -> tuple[int | None, int | None]:
    temple_id = current_temple_id
    if temple_code:
        temple = session.scalar(select(Temple).where(Temple.temple_code == temple_code.upper()))
        if temple is None:
            raise NotFoundError(f"Temple '{temple_code}' not found.")
        temple_id = temple.id
    hall_id = None
    if hall_code:
        hall = session.scalar(select(AnnadhanamHall).where(AnnadhanamHall.hall_code == hall_code))
        if hall is None:
            raise NotFoundError(f"Hall '{hall_code}' not found.")
        if temple_id is None:
            temple_id = hall.temple_id
        elif hall.temple_id != temple_id:
            raise ValidationError(f"Hall '{hall_code}' does not belong to the assigned temple.")
        hall_id = hall.id
    return temple_id, hall_id


def create_staff(session: Session, payload: StaffCreate) -> Staff:
    """Create a staff member. staff_code is assigned by the PostgreSQL sequence."""
    temple_id, hall_id = _assignment(session, payload.temple_code, payload.hall_code)
    staff = Staff(
        staff_name=payload.staff_name,
        employee_code=payload.employee_code,
        department=payload.department,
        designation=payload.designation,
        shift=payload.shift,
        phone=payload.phone,
        active=payload.active,
        temple_id=temple_id,
        hall_id=hall_id,
    )
    session.add(staff)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            f"Employee code '{payload.employee_code}' is already registered."
        ) from exc
    return get_by_code(session, staff.staff_code)


def update_staff(session: Session, staff_code: str, payload: StaffUpdate) -> Staff:
    staff = get_by_code(session, staff_code)
    changes = payload.model_dump(exclude_unset=True)
    if "temple_code" in changes or "hall_code" in changes:
        temple_code = changes.pop("temple_code", None)
        hall_code = changes.pop("hall_code", None)
        if temple_code is None and "hall_code" in payload.model_fields_set and hall_code is None \
                and "temple_code" not in payload.model_fields_set:
            staff.hall_id = None                      # unassign hall only
        else:
            keep_temple = staff.temple_id if "temple_code" not in payload.model_fields_set else None
            staff.temple_id, staff.hall_id = _assignment(session, temple_code, hall_code, keep_temple)
    for field, value in changes.items():
        if field == "staff_name" and value is None:
            continue
        setattr(staff, field, value)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            f"Staff '{staff_code}' could not be updated: employee code already in use."
        ) from exc
    session.expire_all()
    return get_by_code(session, staff_code)


def delete_staff(session: Session, staff_code: str) -> None:
    """Delete a staff member.

    Blocked by the database when attendance history exists, because that history
    must not disappear. Callers should deactivate instead.
    """
    staff = get_by_code(session, staff_code)
    session.delete(staff)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            f"Staff '{staff_code}' has attendance history and cannot be deleted. "
            "Set active=false instead."
        ) from exc


# ------------------------------------------------------------------ attendance
def list_attendance(
    session: Session,
    *,
    page: int,
    page_size: int,
    staff_code: str | None = None,
    camera_id: str | None = None,
    status: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> PageResult:
    statement = (
        select(StaffAttendance)
        .options(joinedload(StaffAttendance.staff), joinedload(StaffAttendance.camera))
        .order_by(StaffAttendance.entry_time.desc(), StaffAttendance.id.desc())
    )
    if staff_code is not None:
        statement = statement.join(Staff, StaffAttendance.staff_id == Staff.id).where(
            Staff.staff_code == staff_code
        )
    if camera_id is not None:
        statement = statement.join(Camera, StaffAttendance.camera_id == Camera.id).where(
            Camera.camera_id == camera_id
        )
    if status is not None:
        statement = statement.where(StaffAttendance.status == status)
    if start_time is not None:
        statement = statement.where(StaffAttendance.entry_time >= start_time)
    if end_time is not None:
        statement = statement.where(StaffAttendance.entry_time < end_time)
    return paginate(session, statement, page, page_size)


def list_attendance_for_staff(
    session: Session, staff_code: str, *, page: int, page_size: int, **filters
) -> PageResult:
    """Attendance for one staff member. 404s when the staff code is unknown."""
    get_by_code(session, staff_code)
    return list_attendance(
        session, page=page, page_size=page_size, staff_code=staff_code, **filters
    )


def count_present(session: Session) -> int:
    from sqlalchemy import func

    from app.models.enums import AttendanceStatus

    return session.scalar(
        select(func.count())
        .select_from(StaffAttendance)
        .where(StaffAttendance.status == AttendanceStatus.PRESENT)
    ) or 0
