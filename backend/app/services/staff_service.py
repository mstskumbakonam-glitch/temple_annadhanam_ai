"""Staff and attendance business logic."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models import Camera, Staff, StaffAttendance
from app.schemas.staff import StaffCreate, StaffUpdate
from app.services.exceptions import ConflictError, NotFoundError
from app.services.pagination import PageResult, paginate


def _base_query() -> Select:
    return select(Staff).order_by(Staff.staff_code)


def get_by_code(session: Session, staff_code: str) -> Staff:
    staff = session.scalar(select(Staff).where(Staff.staff_code == staff_code))
    if staff is None:
        raise NotFoundError(f"Staff '{staff_code}' not found.")
    return staff


def list_staff(
    session: Session,
    *,
    page: int,
    page_size: int,
    active: bool | None = None,
    department: str | None = None,
) -> PageResult:
    statement = _base_query()
    if active is not None:
        statement = statement.where(Staff.active.is_(active))
    if department is not None:
        statement = statement.where(Staff.department == department)
    return paginate(session, statement, page, page_size)


def create_staff(session: Session, payload: StaffCreate) -> Staff:
    """Create a staff member. staff_code is assigned by the PostgreSQL sequence."""
    staff = Staff(
        staff_name=payload.staff_name,
        employee_code=payload.employee_code,
        department=payload.department,
        active=payload.active,
    )
    session.add(staff)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            f"Employee code '{payload.employee_code}' is already registered."
        ) from exc
    session.refresh(staff)
    return staff


def update_staff(session: Session, staff_code: str, payload: StaffUpdate) -> Staff:
    staff = get_by_code(session, staff_code)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(staff, field, value)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            f"Staff '{staff_code}' could not be updated: employee code already in use."
        ) from exc
    session.refresh(staff)
    return staff


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
