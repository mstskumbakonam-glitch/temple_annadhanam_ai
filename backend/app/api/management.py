"""Temple, hall, seat-status, session, attendance, overview and report endpoints.

Permissions (see docs/MANAGEMENT.md):
    viewer   - read dashboards, temples, halls, seats, sessions, capacity reports
    operator - also change seat status, create/edit/cancel sessions, mark attendance,
               read the attendance register and staff-attendance report
    admin    - also create/edit/delete temples, halls, seat layouts and staff
"""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import PaginationParams, get_db
from app.config import get_settings
from app.models.enums import SeatStatus, SessionStatus
from app.schemas.common import ErrorResponse, HallCode, Page, SeatCode
from app.schemas.management import (
    AttendanceBulk,
    AttendanceRegister,
    AuthInfo,
    HallCreate,
    HallRead,
    HallSeatRead,
    HallUpdate,
    ManagementOverview,
    SeatGenerate,
    SeatGenerateResult,
    SeatStatusChange,
    SeatStatusEventRead,
    SessionCancel,
    SessionCreate,
    SessionRead,
    SessionUpdate,
    TempleCode,
    TempleCreate,
    TempleRead,
    TempleUpdate,
)
from app.security import RequireOperatorWrites, RequireRole, Role, require_operator, role_guard
from app.services import (
    attendance_service,
    overview_service,
    report_service,
    seat_status_service,
    session_service,
    temple_service,
)

DbSession = Annotated[Session, Depends(get_db)]
NOT_FOUND = {404: {"model": ErrorResponse, "description": "Not found"}}
CONFLICT = {409: {"model": ErrorResponse, "description": "Conflict"}}


def tz() -> str:
    return get_settings().site_timezone


# ---------------------------------------------------------------------------
# Who am I
# ---------------------------------------------------------------------------
auth_router = APIRouter(prefix="/api/auth", tags=["system"], dependencies=[RequireRole])


@auth_router.get("/me", response_model=AuthInfo, summary="Role of the presented key")
def me(request: Request) -> AuthInfo:
    s = get_settings()
    return AuthInfo(role=request.state.role.value, auth_required=s.auth_enabled,
                    site_timezone=s.site_timezone, demo_mode=s.demo_mode,
                    app_version=s.app_version, environment=s.app_env)


# ---------------------------------------------------------------------------
# Dashboard overview
# ---------------------------------------------------------------------------
overview_router = APIRouter(tags=["Dashboard"], dependencies=[RequireRole])


@overview_router.get(
    "/api/dashboard/overview", response_model=ManagementOverview, responses=NOT_FOUND,
    summary="Management dashboard figures",
    description="Temples, halls, seat capacity/occupancy, staff attendance and sessions, all counted "
    "from the database. Optional temple scope.",
)
def overview(session: DbSession, temple_code: TempleCode | None = Query(None)) -> ManagementOverview:
    return overview_service.overview(session, tz(), temple_code)


# ---------------------------------------------------------------------------
# Temples
# ---------------------------------------------------------------------------
temples_router = APIRouter(prefix="/api/temples", tags=["Temples"], dependencies=[RequireRole])


@temples_router.get("", response_model=Page[TempleRead], summary="List temples")
def list_temples(session: DbSession, pagination: PaginationParams,
                 search: str | None = Query(None, max_length=80),
                 district: str | None = Query(None, max_length=80),
                 active: bool | None = Query(None)) -> Page[TempleRead]:
    items, r = temple_service.list_temples(session, page=pagination.page, page_size=pagination.page_size,
                                           search=search, district=district, active=active)
    return Page[TempleRead](items=items, page=r.page, page_size=r.page_size, total=r.total)


@temples_router.get("/districts", response_model=list[str], summary="Districts in use")
def list_districts(session: DbSession) -> list[str]:
    return temple_service.districts(session)


@temples_router.post("", response_model=TempleRead, status_code=status.HTTP_201_CREATED,
                     responses=CONFLICT, summary="Add a temple (admin)")
def create_temple(payload: TempleCreate, session: DbSession) -> TempleRead:
    return temple_service.temple_read(session, temple_service.create_temple(session, payload))


@temples_router.get("/{temple_code}", response_model=TempleRead, responses=NOT_FOUND)
def get_temple(temple_code: TempleCode, session: DbSession) -> TempleRead:
    return temple_service.temple_read(session, temple_service.get_temple(session, temple_code))


@temples_router.put("/{temple_code}", response_model=TempleRead, responses=NOT_FOUND,
                    summary="Edit a temple (admin)")
def update_temple(temple_code: TempleCode, payload: TempleUpdate, session: DbSession) -> TempleRead:
    return temple_service.temple_read(session, temple_service.update_temple(session, temple_code, payload))


@temples_router.delete("/{temple_code}", status_code=status.HTTP_204_NO_CONTENT,
                       responses={**NOT_FOUND, **CONFLICT},
                       summary="Delete a temple without halls (admin)")
def delete_temple(temple_code: TempleCode, session: DbSession) -> Response:
    temple_service.delete_temple(session, temple_code)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Halls (admin writes) and seat layout
# ---------------------------------------------------------------------------
halls_router = APIRouter(prefix="/api/halls", tags=["Halls"], dependencies=[RequireRole])


@halls_router.get("", response_model=Page[HallRead], summary="List halls")
def list_halls(session: DbSession, pagination: PaginationParams,
               temple_code: TempleCode | None = Query(None),
               search: str | None = Query(None, max_length=80),
               active: bool | None = Query(None)) -> Page[HallRead]:
    items, r = temple_service.list_halls(session, page=pagination.page, page_size=pagination.page_size,
                                         temple_code=temple_code, search=search, active=active)
    return Page[HallRead](items=items, page=r.page, page_size=r.page_size, total=r.total)


@halls_router.post("", response_model=HallRead, status_code=status.HTTP_201_CREATED,
                   responses={**NOT_FOUND, **CONFLICT}, summary="Add a hall to a temple (admin)")
def create_hall(payload: HallCreate, session: DbSession) -> HallRead:
    return temple_service.create_hall(session, payload)


@halls_router.get("/{hall_code}", response_model=HallRead, responses=NOT_FOUND)
def get_hall(hall_code: HallCode, session: DbSession) -> HallRead:
    hall = temple_service.get_hall(session, hall_code)
    return temple_service.hall_read(hall, temple_service.seat_counts_by_hall(session, [hall_code]))


@halls_router.put("/{hall_code}", response_model=HallRead, responses=NOT_FOUND, summary="Edit a hall (admin)")
def update_hall(hall_code: HallCode, payload: HallUpdate, session: DbSession) -> HallRead:
    return temple_service.update_hall(session, hall_code, payload)


@halls_router.delete("/{hall_code}", status_code=status.HTTP_204_NO_CONTENT,
                     responses={**NOT_FOUND, **CONFLICT}, summary="Delete an empty hall (admin)")
def delete_hall(hall_code: HallCode, session: DbSession) -> Response:
    temple_service.delete_hall(session, hall_code)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@halls_router.post("/{hall_code}/seats/generate", response_model=SeatGenerateResult,
                   responses=NOT_FOUND, summary="Create a grid of seats (admin)",
                   description="Adds seats <prefix>001.. row by row; codes that already exist are skipped.")
def generate_seats(hall_code: HallCode, spec: SeatGenerate, session: DbSession) -> SeatGenerateResult:
    return seat_status_service.generate_layout(session, hall_code, spec)


# ---------------------------------------------------------------------------
# Seat status (operators may write)
# ---------------------------------------------------------------------------
seat_ops_router = APIRouter(prefix="/api/halls", tags=["Seat Management"],
                            dependencies=[RequireOperatorWrites])


@seat_ops_router.get("/{hall_code}/seats", response_model=list[HallSeatRead], responses=NOT_FOUND,
                     summary="Seats of a hall with their confirmed status")
def hall_seats(hall_code: HallCode, session: DbSession,
               status_filter: SeatStatus | None = Query(None, alias="status"),
               include_disabled: bool = Query(False)) -> list[HallSeatRead]:
    return seat_status_service.list_hall_seats(session, hall_code, status=status_filter,
                                               include_disabled=include_disabled)


@seat_ops_router.get("/{hall_code}/seats/history", response_model=Page[SeatStatusEventRead],
                     responses=NOT_FOUND, summary="Seat status change history")
def seat_history(hall_code: HallCode, session: DbSession, pagination: PaginationParams,
                 seat_id: SeatCode | None = Query(None)) -> Page[SeatStatusEventRead]:
    temple_service.get_hall(session, hall_code)
    items, r = seat_status_service.history(session, page=pagination.page, page_size=pagination.page_size,
                                           hall_code=hall_code, seat_code=seat_id)
    return Page[SeatStatusEventRead](items=items, page=r.page, page_size=r.page_size, total=r.total)


@seat_ops_router.post(
    "/{hall_code}/seats/{seat_id}/status", response_model=HallSeatRead,
    responses={**NOT_FOUND, **CONFLICT},
    summary="Change a seat's status (operator)",
    description="Occupy, release, reserve, cancel a reservation, or take out of / return to service. "
    "Invalid transitions and simultaneous changes are rejected with 409.",
)
def change_seat_status(hall_code: HallCode, seat_id: SeatCode, change: SeatStatusChange,
                       session: DbSession, request: Request) -> HallSeatRead:
    return seat_status_service.change_status(session, hall_code, seat_id, change,
                                             actor_role=request.state.role.value)


# ---------------------------------------------------------------------------
# Sessions (operators may write)
# ---------------------------------------------------------------------------
sessions_router = APIRouter(prefix="/api/sessions", tags=["Sessions"], dependencies=[RequireOperatorWrites])


@sessions_router.get("", response_model=Page[SessionRead], summary="List / calendar of sessions")
def list_sessions(session: DbSession, pagination: PaginationParams,
                  start_date: date | None = Query(None), end_date: date | None = Query(None),
                  temple_code: TempleCode | None = Query(None), hall_code: HallCode | None = Query(None),
                  status_filter: SessionStatus | None = Query(None, alias="status"),
                  timing: str | None = Query(None, pattern="^(upcoming|past)$")) -> Page[SessionRead]:
    r = session_service.list_sessions(session, page=pagination.page, page_size=pagination.page_size,
                                      start_date=start_date, end_date=end_date, temple_code=temple_code,
                                      hall_code=hall_code, status=status_filter, timing=timing)
    return Page[SessionRead](items=session_service.reads(session, r.items, tz()), page=r.page,
                             page_size=r.page_size, total=r.total)


@sessions_router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED,
                      responses={**NOT_FOUND, **CONFLICT}, summary="Schedule a session (operator)")
def create_session(payload: SessionCreate, session: DbSession) -> SessionRead:
    return session_service.create(session, payload, tz())


@sessions_router.get("/{session_id}", response_model=SessionRead, responses=NOT_FOUND)
def get_session(session_id: int, session: DbSession) -> SessionRead:
    return session_service.reads(session, [session_service.get(session, session_id)], tz())[0]


@sessions_router.put("/{session_id}", response_model=SessionRead, responses={**NOT_FOUND, **CONFLICT},
                     summary="Edit a session or record its status / actual attendance (operator)")
def update_session(session_id: int, payload: SessionUpdate, session: DbSession) -> SessionRead:
    return session_service.update(session, session_id, payload, tz())


@sessions_router.post("/{session_id}/cancel", response_model=SessionRead,
                      responses={**NOT_FOUND, **CONFLICT}, summary="Cancel a session (operator)")
def cancel_session(session_id: int, payload: SessionCancel, session: DbSession) -> SessionRead:
    return session_service.cancel(session, session_id, payload, tz())


# ---------------------------------------------------------------------------
# Daily attendance register (operator read and write: it lists staff names)
# ---------------------------------------------------------------------------
attendance_router = APIRouter(prefix="/api/attendance", tags=["Attendance"],
                              dependencies=[Depends(role_guard(Role.OPERATOR, read_role=Role.OPERATOR))])


@attendance_router.get("", response_model=AttendanceRegister, summary="Attendance register for a day")
def attendance_register(session: DbSession, day: date | None = Query(None, alias="date"),
                        temple_code: TempleCode | None = Query(None),
                        hall_code: HallCode | None = Query(None)) -> AttendanceRegister:
    return attendance_service.register(session, day or session_service.local_today(tz()),
                                       temple_code=temple_code, hall_code=hall_code)


@attendance_router.put("", summary="Mark attendance for one or more staff (operator)")
def mark_attendance(payload: AttendanceBulk, session: DbSession) -> dict:
    return {"saved": attendance_service.mark(session, payload, tz())}


# ---------------------------------------------------------------------------
# Reports (JSON or CSV)
# ---------------------------------------------------------------------------
reports_router = APIRouter(prefix="/api/reports", tags=["Reports"], dependencies=[RequireRole])


def _respond(name: str, report, fmt: str):
    columns, rows = report
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # Neutralise spreadsheet formulas in text cells (CSV injection).
            writer.writerow({k: (f"'{v}" if isinstance(v, str) and v[:1] in "=+-@" else v)
                             for k, v in row.items()})
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    return {"report": name, "columns": columns, "rows": rows}


Fmt = Query("json", pattern="^(json|csv)$")


def _dates(start: date | None, end: date | None) -> tuple[date, date]:
    d_start, d_end = report_service.default_range(tz())
    return start or d_start, end or d_end


@reports_router.get("/temple-capacity", summary="Temple-wise seat capacity")
def report_temple_capacity(session: DbSession, temple_code: TempleCode | None = Query(None), format: str = Fmt):
    return _respond("temple_capacity", report_service.temple_capacity(session, temple_code), format)


@reports_router.get("/hall-occupancy", summary="Hall-wise current occupancy")
def report_hall_occupancy(session: DbSession, temple_code: TempleCode | None = Query(None), format: str = Fmt):
    return _respond("hall_occupancy", report_service.hall_occupancy(session, temple_code), format)


@reports_router.get("/sessions", summary="Session schedule (with expected vs actual where recorded)")
def report_sessions(session: DbSession, start_date: date | None = Query(None),
                    end_date: date | None = Query(None), temple_code: TempleCode | None = Query(None),
                    hall_code: HallCode | None = Query(None),
                    actual_recorded_only: bool = Query(False), format: str = Fmt):
    s, e = _dates(start_date, end_date)
    return _respond("sessions", report_service.sessions(
        session, s, e, tz(), temple_code=temple_code, hall_code=hall_code,
        actual_recorded_only=actual_recorded_only), format)


@reports_router.get("/staff-attendance", summary="Staff attendance (operator)",
                    dependencies=[Depends(require_operator)])
def report_staff_attendance(session: DbSession, start_date: date | None = Query(None),
                            end_date: date | None = Query(None),
                            temple_code: TempleCode | None = Query(None), format: str = Fmt):
    s, e = _dates(start_date, end_date)
    return _respond("staff_attendance", report_service.staff_attendance(session, s, e, tz(), temple_code), format)


@reports_router.get("/seat-occupancy", summary="Historical seat occupancy per day and hall")
def report_seat_occupancy(session: DbSession, start_date: date | None = Query(None),
                          end_date: date | None = Query(None), hall_code: HallCode | None = Query(None),
                          format: str = Fmt):
    s, e = _dates(start_date, end_date)
    return _respond("seat_occupancy", report_service.seat_occupancy_history(session, s, e, tz(), hall_code), format)


ROUTERS = [auth_router, overview_router, temples_router, halls_router, seat_ops_router,
           sessions_router, attendance_router, reports_router]
