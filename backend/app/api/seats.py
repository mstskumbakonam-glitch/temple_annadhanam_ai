"""Seat endpoints.

/api/seats/status and /api/seats/history are declared before /api/seats/{seat_id}.

seat_id is unique per hall, so single-seat routes take an optional hall_id query
parameter defaulting to MAIN.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.security import RequireRole
from app.api.deps import PaginationParams, TimeRangeParams, get_db
from app.models.enums import PersonType
from app.schemas.common import CameraCode, ErrorResponse, HallCode, Page, SeatCode
from app.schemas.seat import (
    SeatCreate,
    SeatHistoryRead,
    SeatRead,
    SeatStatusRead,
    SeatUpdate,
)
from app.services import seat_service

router = APIRouter(prefix="/api/seats", tags=["Seats"], dependencies=[RequireRole])

DbSession = Annotated[Session, Depends(get_db)]
NOT_FOUND = {404: {"model": ErrorResponse, "description": "Seat not found"}}
CONFLICT = {409: {"model": ErrorResponse, "description": "Seat already exists in this hall"}}

HallQuery = Query(
    seat_service.DEFAULT_HALL, description="Hall the seat belongs to. Seat IDs repeat per hall."
)


def _seat_read(seat) -> SeatRead:
    return SeatRead(
        seat_id=seat.seat_id,
        hall_id=seat.hall_id,
        seat_label=seat.seat_label,
        row_number=seat.row_number,
        column_number=seat.column_number,
        camera_id=seat.camera.camera_id if seat.camera else None,
        polygon_points=seat.polygon_points,
        enabled=seat.enabled,
        created_at=seat.created_at,
        updated_at=seat.updated_at,
    )


@router.get("", response_model=Page[SeatRead], summary="List seats")
def list_seats(
    session: DbSession,
    pagination: PaginationParams,
    hall_id: HallCode | None = Query(None),
    camera_id: CameraCode | None = Query(None),
    enabled: bool | None = Query(None),
) -> Page[SeatRead]:
    result = seat_service.list_seats(
        session,
        page=pagination.page,
        page_size=pagination.page_size,
        hall_id=hall_id,
        camera_id=camera_id,
        enabled=enabled,
    )
    return Page[SeatRead](
        items=[_seat_read(s) for s in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get(
    "/status",
    response_model=list[SeatStatusRead],
    summary="Live seat occupancy",
    description="OCCUPIED or EMPTY for every enabled seat, for the seat map. "
    "visitor_code and staff_code are null when a seat is empty.",
)
def seat_status(
    session: DbSession,
    hall_id: HallCode | None = Query(None),
    camera_id: CameraCode | None = Query(None),
) -> list[SeatStatusRead]:
    return seat_service.list_seat_status(session, hall_id=hall_id, camera_id=camera_id)


@router.get(
    "/history",
    response_model=Page[SeatHistoryRead],
    summary="Seat occupancy history",
)
def seat_history(
    session: DbSession,
    pagination: PaginationParams,
    time_range: TimeRangeParams,
    seat_id: SeatCode | None = Query(None),
    hall_id: HallCode | None = Query(None),
    camera_id: CameraCode | None = Query(None),
    person_type: PersonType | None = Query(None),
) -> Page[SeatHistoryRead]:
    result = seat_service.list_seat_history(
        session,
        page=pagination.page,
        page_size=pagination.page_size,
        seat_id=seat_id,
        hall_id=hall_id,
        camera_id=camera_id,
        person_type=person_type,
        start_time=time_range.start_time,
        end_time=time_range.end_time,
    )
    return Page[SeatHistoryRead](
        items=[
            SeatHistoryRead(
                seat_id=row.seat.seat_id,
                hall_id=row.seat.hall_id,
                camera_id=row.camera.camera_id if row.camera else None,
                person_type=row.person_type,
                visitor_code=row.visitor.visitor_code if row.visitor else None,
                staff_code=row.staff.staff_code if row.staff else None,
                occupied_at=row.occupied_at,
                released_at=row.released_at,
                duration_seconds=row.duration_seconds,
                status=row.status,
            )
            for row in result.items
        ],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.post(
    "",
    response_model=SeatRead,
    status_code=status.HTTP_201_CREATED,
    responses=CONFLICT,
    summary="Add a seat",
    description="Seat polygons are supplied here, never hard-coded in AI code.",
)
def create_seat(payload: SeatCreate, session: DbSession) -> SeatRead:
    return _seat_read(seat_service.create_seat(session, payload))


@router.get("/{seat_id}", response_model=SeatRead, responses=NOT_FOUND, summary="Get one seat")
def get_seat(
    seat_id: SeatCode, session: DbSession, hall_id: HallCode = HallQuery
) -> SeatRead:
    return _seat_read(seat_service.get_by_code(session, seat_id, hall_id))


@router.put(
    "/{seat_id}",
    response_model=SeatRead,
    responses={**NOT_FOUND, **CONFLICT},
    summary="Update a seat",
)
def update_seat(
    seat_id: SeatCode,
    payload: SeatUpdate,
    session: DbSession,
    hall_id: HallCode = HallQuery,
) -> SeatRead:
    return _seat_read(seat_service.update_seat(session, seat_id, payload, hall_id))


@router.delete(
    "/{seat_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**NOT_FOUND, **CONFLICT},
    summary="Delete a seat",
    description="Rejected with 409 when occupancy history exists. Disable it instead.",
)
def delete_seat(
    seat_id: SeatCode, session: DbSession, hall_id: HallCode = HallQuery
) -> Response:
    seat_service.delete_seat(session, seat_id, hall_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
