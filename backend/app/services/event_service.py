"""Camera event queries. Read-only in this phase."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import CameraEvent
from app.services import camera_service
from app.services.pagination import PageResult, paginate


def list_camera_events(
    session: Session,
    camera_id: str,
    *,
    page: int,
    page_size: int,
    event_type: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> PageResult:
    """Events for one camera. 404s when the camera code is unknown."""
    camera = camera_service.get_by_code(session, camera_id)

    statement = (
        select(CameraEvent)
        .options(joinedload(CameraEvent.camera))
        .where(CameraEvent.camera_id == camera.id)
        .order_by(CameraEvent.event_time.desc(), CameraEvent.id.desc())
    )
    if event_type is not None:
        statement = statement.where(CameraEvent.event_type == event_type)
    if start_time is not None:
        statement = statement.where(CameraEvent.event_time >= start_time)
    if end_time is not None:
        statement = statement.where(CameraEvent.event_time < end_time)
    return paginate(session, statement, page, page_size)
