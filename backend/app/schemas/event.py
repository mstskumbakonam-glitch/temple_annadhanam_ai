"""Event schemas. Read-only: events are written by the AI pipeline, not by clients."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import CameraEventType, VisitorEventType
from app.schemas.common import CameraCode, VisitorCode


class VisitorEventRead(BaseModel):
    visitor_code: VisitorCode | None = None
    camera_id: CameraCode | None = None
    event_type: VisitorEventType
    event_time: datetime
    tracking_id: int | None = None
    metadata: dict[str, Any] | None = Field(
        default=None, description="Free-form detail recorded with the event."
    )


class CameraEventRead(BaseModel):
    camera_id: CameraCode
    event_type: CameraEventType
    event_time: datetime
    fps: float | None = None
    message: str | None = None
    metadata: dict[str, Any] | None = None
