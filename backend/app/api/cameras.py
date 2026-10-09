"""Camera endpoints.

Route order matters: the literal path /api/cameras/status is declared before
/api/cameras/{camera_id}, otherwise "status" would be captured as a camera code.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import PaginationParams, TimeRangeParams, get_db
from app.models.enums import CameraEventType, CameraStatus
from app.schemas.ai import CameraAIStatus
from app.schemas.camera import CameraCreate, CameraRead, CameraStatusRead, CameraUpdate
from app.schemas.common import CameraCode, ErrorResponse, Page
from app.schemas.event import CameraEventRead
from app.services import ai_status_service, camera_service, event_service

router = APIRouter(prefix="/api/cameras", tags=["Cameras"])

DbSession = Annotated[Session, Depends(get_db)]

NOT_FOUND = {404: {"model": ErrorResponse, "description": "Camera not found"}}
CONFLICT = {409: {"model": ErrorResponse, "description": "Camera code already exists"}}


@router.get(
    "",
    response_model=Page[CameraRead],
    summary="List cameras",
    description="Paginated list of configured cameras, optionally filtered by status.",
)
def list_cameras(
    session: DbSession,
    pagination: PaginationParams,
    status_filter: CameraStatus | None = Query(None, alias="status"),
    enabled: bool | None = Query(None),
) -> Page[CameraRead]:
    result = camera_service.list_cameras(
        session,
        page=pagination.page,
        page_size=pagination.page_size,
        status=status_filter,
        enabled=enabled,
    )
    return Page[CameraRead](
        items=[CameraRead.model_validate(c) for c in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get(
    "/status",
    response_model=list[CameraStatusRead],
    summary="Camera status overview",
    description="Compact status of every camera, for the dashboard grid.",
)
def camera_status(session: DbSession) -> list[CameraStatusRead]:
    return [CameraStatusRead.model_validate(c) for c in camera_service.list_status(session)]


@router.post(
    "",
    response_model=CameraRead,
    status_code=status.HTTP_201_CREATED,
    responses=CONFLICT,
    summary="Add a camera",
    description="Register a camera. RTSP credentials are stored but never returned.",
)
def create_camera(payload: CameraCreate, session: DbSession) -> CameraRead:
    return CameraRead.model_validate(camera_service.create_camera(session, payload))


@router.get(
    "/{camera_id}",
    response_model=CameraRead,
    responses=NOT_FOUND,
    summary="Get one camera",
)
def get_camera(camera_id: CameraCode, session: DbSession) -> CameraRead:
    return CameraRead.model_validate(camera_service.get_by_code(session, camera_id))


@router.put(
    "/{camera_id}",
    response_model=CameraRead,
    responses={**NOT_FOUND, **CONFLICT},
    summary="Update a camera",
    description="Partial update: only the fields supplied are changed.",
)
def update_camera(
    camera_id: CameraCode, payload: CameraUpdate, session: DbSession
) -> CameraRead:
    return CameraRead.model_validate(camera_service.update_camera(session, camera_id, payload))


@router.delete(
    "/{camera_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**NOT_FOUND, **CONFLICT},
    summary="Delete a camera",
    description="Rejected with 409 when historical events still reference the camera.",
)
def delete_camera(camera_id: CameraCode, session: DbSession) -> Response:
    camera_service.delete_camera(session, camera_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{camera_id}/events",
    response_model=Page[CameraEventRead],
    responses=NOT_FOUND,
    tags=["Events"],
    summary="Camera event history",
)
def camera_events(
    camera_id: CameraCode,
    session: DbSession,
    pagination: PaginationParams,
    time_range: TimeRangeParams,
    event_type: CameraEventType | None = Query(None),
) -> Page[CameraEventRead]:
    result = event_service.list_camera_events(
        session,
        camera_id,
        page=pagination.page,
        page_size=pagination.page_size,
        event_type=event_type,
        start_time=time_range.start_time,
        end_time=time_range.end_time,
    )
    return Page[CameraEventRead](
        items=[
            CameraEventRead(
                camera_id=event.camera.camera_id,
                event_type=event.event_type,
                event_time=event.event_time,
                fps=event.fps,
                message=event.message,
                metadata=event.event_metadata,
            )
            for event in result.items
        ],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get(
    "/{camera_id}/ai-status",
    response_model=CameraAIStatus,
    responses=NOT_FOUND,
    tags=["AI Runtime"],
    summary="AI runtime status of one camera",
    description="Connection state, person count, tracks and FPS of the running worker. "
    "If no worker is running for this camera, ai_running is false and counts are null.",
)
def camera_ai_status(camera_id: CameraCode, session: DbSession) -> CameraAIStatus:
    return ai_status_service.get_camera_ai_status(session, camera_id)
