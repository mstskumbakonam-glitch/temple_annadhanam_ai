"""Visitor response schemas.

Visitors are anonymous. There is no create/update schema and no face data:
visitor records are produced by the AI pipeline in later phases.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import VisitorStatus
from app.schemas.common import CameraCode, SeatCode, VisitorCode


class VisitorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    visitor_code: VisitorCode
    tracking_id: int | None = None
    camera_id: CameraCode | None = None
    entry_time: datetime | None = None
    exit_time: datetime | None = None
    last_seen: datetime | None = None
    status: VisitorStatus
    current_seat_id: SeatCode | None = None
