"""Dashboard and hall occupancy schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import HallCode


class DashboardSummary(BaseModel):
    """Headline counters. Every value is computed from PostgreSQL."""

    total_cameras: int = Field(ge=0)
    online_cameras: int = Field(ge=0)
    offline_cameras: int = Field(ge=0)
    current_visitors: int = Field(ge=0, description="Visitors not yet marked EXITED.")
    today_entries: int = Field(ge=0)
    today_exits: int = Field(ge=0)
    staff_present: int = Field(ge=0)
    total_seats: int = Field(ge=0)
    occupied_seats: int = Field(ge=0)
    empty_seats: int = Field(ge=0)
    occupancy_percentage: float = Field(ge=0, le=100)


class HallOccupancy(BaseModel):
    hall_id: HallCode
    total_seats: int = Field(ge=0)
    occupied_seats: int = Field(ge=0)
    empty_seats: int = Field(ge=0)
    occupancy_percentage: float = Field(
        ge=0, le=100, description="occupied / total * 100, or 0.0 when the hall has no seats."
    )
