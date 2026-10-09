"""Visitor endpoints. Read-only: visitor records come from the AI pipeline.

/api/visitors/current and /api/visitors/today are declared before
/api/visitors/{visitor_code} so they are not captured as visitor codes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import PaginationParams, TimeRangeParams, get_db
from app.models.enums import VisitorStatus
from app.schemas.common import CameraCode, ErrorResponse, Page, VisitorCode
from app.schemas.event import VisitorEventRead
from app.schemas.visitor import VisitorRead
from app.services import visitor_service

router = APIRouter(prefix="/api/visitors", tags=["Visitors"])

DbSession = Annotated[Session, Depends(get_db)]
NOT_FOUND = {404: {"model": ErrorResponse, "description": "Visitor not found"}}


def _to_read(visitor) -> VisitorRead:
    """Map the ORM object to the API shape, exposing codes rather than row IDs."""
    return VisitorRead(
        visitor_code=visitor.visitor_code,
        tracking_id=visitor.tracking_id,
        camera_id=visitor.camera.camera_id if visitor.camera else None,
        entry_time=visitor.entry_time,
        exit_time=visitor.exit_time,
        last_seen=visitor.last_seen,
        status=visitor.status,
        current_seat_id=visitor.current_seat.seat_id if visitor.current_seat else None,
    )


def _page(result) -> Page[VisitorRead]:
    return Page[VisitorRead](
        items=[_to_read(v) for v in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get(
    "",
    response_model=Page[VisitorRead],
    summary="List visitors",
    description="Paginated visitor sessions with optional status, camera and time filters.",
)
def list_visitors(
    session: DbSession,
    pagination: PaginationParams,
    time_range: TimeRangeParams,
    status_filter: VisitorStatus | None = Query(None, alias="status"),
    camera_id: CameraCode | None = Query(None),
) -> Page[VisitorRead]:
    return _page(
        visitor_service.list_visitors(
            session,
            page=pagination.page,
            page_size=pagination.page_size,
            status=status_filter,
            camera_id=camera_id,
            start_time=time_range.start_time,
            end_time=time_range.end_time,
        )
    )


@router.get(
    "/current",
    response_model=Page[VisitorRead],
    summary="Visitors currently inside",
    description="Visitor sessions that have not yet been closed by an exit.",
)
def current_visitors(session: DbSession, pagination: PaginationParams) -> Page[VisitorRead]:
    return _page(
        visitor_service.list_current(
            session, page=pagination.page, page_size=pagination.page_size
        )
    )


@router.get(
    "/today",
    response_model=Page[VisitorRead],
    summary="Today's visitors",
    description="Visitors who entered or exited during the current UTC day.",
)
def today_visitors(session: DbSession, pagination: PaginationParams) -> Page[VisitorRead]:
    return _page(
        visitor_service.list_today(
            session, page=pagination.page, page_size=pagination.page_size
        )
    )


@router.get(
    "/{visitor_code}",
    response_model=VisitorRead,
    responses=NOT_FOUND,
    summary="Get one visitor",
)
def get_visitor(visitor_code: VisitorCode, session: DbSession) -> VisitorRead:
    return _to_read(visitor_service.get_by_code(session, visitor_code))


@router.get(
    "/{visitor_code}/events",
    response_model=Page[VisitorEventRead],
    responses=NOT_FOUND,
    tags=["Events"],
    summary="Visitor event timeline",
)
def visitor_events(
    visitor_code: VisitorCode, session: DbSession, pagination: PaginationParams
) -> Page[VisitorEventRead]:
    result = visitor_service.list_events(
        session, visitor_code, page=pagination.page, page_size=pagination.page_size
    )
    return Page[VisitorEventRead](
        items=[
            VisitorEventRead(
                visitor_code=visitor_code,
                camera_id=event.camera.camera_id if event.camera else None,
                event_type=event.event_type,
                event_time=event.event_time,
                tracking_id=event.tracking_id,
                metadata=event.event_metadata,
            )
            for event in result.items
        ],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )
