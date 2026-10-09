"""Staff and attendance endpoints.

face_embedding is never part of any response model here: enrollment belongs to
the face-recognition phase.

/api/staff/attendance is declared before /api/staff/{staff_code}.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from fastapi import Depends, Request

from app.security import Role, role_guard
from app.api.deps import PaginationParams, TimeRangeParams, get_db
from app.models.enums import AttendanceStatus
from app.schemas.common import CameraCode, ErrorResponse, Page, StaffCode
from app.schemas.staff import AttendanceRead, StaffCreate, StaffRead, StaffUpdate
from app.services import staff_service

router = APIRouter(prefix="/api/staff", tags=["Staff"], dependencies=[Depends(role_guard(Role.ADMIN, read_role=Role.OPERATOR))])


def _is_admin(request: Request) -> bool:
    return getattr(request.state, "role", None) is Role.ADMIN


def _read(session, staff, request: Request) -> StaffRead:
    """Phone numbers are personal data: only admins receive them."""
    from app.services.session_service import local_today
    from app.config import get_settings

    today = staff_service.today_statuses(session, [staff.id], local_today(get_settings().site_timezone))
    return staff_service.to_read(staff, today.get(staff.id), show_phone=_is_admin(request))

DbSession = Annotated[Session, Depends(get_db)]
NOT_FOUND = {404: {"model": ErrorResponse, "description": "Staff not found"}}
CONFLICT = {409: {"model": ErrorResponse, "description": "Employee code already in use"}}


def _attendance_read(record) -> AttendanceRead:
    return AttendanceRead(
        staff_code=record.staff.staff_code,
        staff_name=record.staff.staff_name,
        camera_id=record.camera.camera_id if record.camera else None,
        entry_time=record.entry_time,
        exit_time=record.exit_time,
        last_seen=record.last_seen,
        status=record.status,
    )


def _attendance_page(result) -> Page[AttendanceRead]:
    return Page[AttendanceRead](
        items=[_attendance_read(r) for r in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("", response_model=Page[StaffRead], summary="List staff")
def list_staff(
    request: Request,
    session: DbSession,
    pagination: PaginationParams,
    active: bool | None = Query(None),
    department: str | None = Query(None, max_length=128),
    search: str | None = Query(None, max_length=80),
    temple_code: str | None = Query(None, max_length=32),
    hall_code: str | None = Query(None, max_length=64),
    shift: str | None = Query(None, max_length=16),
) -> Page[StaffRead]:
    from app.config import get_settings
    from app.services.session_service import local_today

    result = staff_service.list_staff(
        session,
        page=pagination.page,
        page_size=pagination.page_size,
        active=active,
        department=department,
        search=search,
        temple_code=temple_code.upper() if temple_code else None,
        hall_code=hall_code.upper() if hall_code else None,
        shift=shift.upper() if shift else None,
    )
    today = staff_service.today_statuses(session, [s.id for s in result.items],
                                         local_today(get_settings().site_timezone))
    return Page[StaffRead](
        items=[staff_service.to_read(s, today.get(s.id), show_phone=_is_admin(request))
               for s in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get(
    "/attendance",
    response_model=Page[AttendanceRead],
    tags=["Attendance"],
    summary="List staff attendance",
    description="Attendance records across all staff, with optional filters.",
)
def list_attendance(
    session: DbSession,
    pagination: PaginationParams,
    time_range: TimeRangeParams,
    staff_code: StaffCode | None = Query(None),
    camera_id: CameraCode | None = Query(None),
    status_filter: AttendanceStatus | None = Query(None, alias="status"),
) -> Page[AttendanceRead]:
    return _attendance_page(
        staff_service.list_attendance(
            session,
            page=pagination.page,
            page_size=pagination.page_size,
            staff_code=staff_code,
            camera_id=camera_id,
            status=status_filter,
            start_time=time_range.start_time,
            end_time=time_range.end_time,
        )
    )


@router.post(
    "",
    response_model=StaffRead,
    status_code=status.HTTP_201_CREATED,
    responses=CONFLICT,
    summary="Register a staff member",
    description="staff_code (STAFF-001) is assigned by PostgreSQL, not by the client.",
)
def create_staff(payload: StaffCreate, session: DbSession, request: Request) -> StaffRead:
    return _read(session, staff_service.create_staff(session, payload), request)


@router.get(
    "/{staff_code}", response_model=StaffRead, responses=NOT_FOUND, summary="Get one staff member"
)
def get_staff(staff_code: StaffCode, session: DbSession, request: Request) -> StaffRead:
    return _read(session, staff_service.get_by_code(session, staff_code), request)


@router.put(
    "/{staff_code}",
    response_model=StaffRead,
    responses={**NOT_FOUND, **CONFLICT},
    summary="Update a staff member",
)
def update_staff(
    staff_code: StaffCode, payload: StaffUpdate, session: DbSession, request: Request
) -> StaffRead:
    return _read(session, staff_service.update_staff(session, staff_code, payload), request)


@router.delete(
    "/{staff_code}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**NOT_FOUND, **CONFLICT},
    summary="Delete a staff member",
    description="Rejected with 409 when attendance history exists. Deactivate instead.",
)
def delete_staff(staff_code: StaffCode, session: DbSession) -> Response:
    staff_service.delete_staff(session, staff_code)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{staff_code}/attendance",
    response_model=Page[AttendanceRead],
    responses=NOT_FOUND,
    tags=["Attendance"],
    summary="Attendance for one staff member",
)
def staff_attendance(
    staff_code: StaffCode,
    session: DbSession,
    pagination: PaginationParams,
    time_range: TimeRangeParams,
    status_filter: AttendanceStatus | None = Query(None, alias="status"),
) -> Page[AttendanceRead]:
    return _attendance_page(
        staff_service.list_attendance_for_staff(
            session,
            staff_code,
            page=pagination.page,
            page_size=pagination.page_size,
            status=status_filter,
            start_time=time_range.start_time,
            end_time=time_range.end_time,
        )
    )
