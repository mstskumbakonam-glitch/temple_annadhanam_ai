"""Seat, seat status and seat history schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import PersonType
from app.schemas.common import CameraCode, HallCode, SeatCode, StaffCode, VisitorCode


class PolygonPoint(BaseModel):
    """One vertex of a seat polygon, in pixel coordinates of the camera frame."""

    x: float = Field(ge=0)
    y: float = Field(ge=0)


class SeatBase(BaseModel):
    seat_label: str | None = Field(default=None, max_length=64)
    row_number: int | None = Field(default=None, ge=0)
    column_number: int | None = Field(default=None, ge=0)
    camera_id: CameraCode | None = Field(
        default=None, description="Code of the camera that observes this seat."
    )
    polygon_points: list[PolygonPoint] | None = Field(
        default=None,
        description="Detection polygon. Configured per deployment, never hard-coded.",
    )
    enabled: bool = True

    @field_validator("polygon_points")
    @classmethod
    def _min_three_points(
        cls, value: list[PolygonPoint] | None
    ) -> list[PolygonPoint] | None:
        if value is not None and len(value) < 3:
            raise ValueError("polygon_points needs at least 3 points to form an area")
        return value


class SeatCreate(SeatBase):
    seat_id: SeatCode = Field(description="Seat code, e.g. S01. Unique within its hall.")
    hall_id: HallCode = Field(default="MAIN")


class SeatUpdate(SeatBase):
    """Partial update. seat_id and hall_id are immutable."""

    seat_label: str | None = None
    enabled: bool | None = None


class SeatRead(SeatBase):
    model_config = ConfigDict(from_attributes=True)

    seat_id: SeatCode
    hall_id: HallCode
    created_at: datetime
    updated_at: datetime


class SeatStatusRead(BaseModel):
    """Live occupancy state of one seat, for the seat map."""

    seat_id: SeatCode
    hall_id: HallCode
    camera_id: CameraCode | None = None
    seat_label: str | None = None
    status: Literal["OCCUPIED", "EMPTY"]
    person_type: PersonType | None = None
    visitor_code: VisitorCode | None = None
    staff_code: StaffCode | None = None
    occupied_at: datetime | None = None
    duration_seconds: int | None = Field(
        default=None, description="Seconds occupied so far. Null when the seat is empty."
    )


class SeatHistoryRead(BaseModel):
    """One completed or ongoing occupancy period."""

    seat_id: SeatCode
    hall_id: HallCode
    camera_id: CameraCode | None = None
    person_type: PersonType
    visitor_code: VisitorCode | None = None
    staff_code: StaffCode | None = None
    occupied_at: datetime
    released_at: datetime | None = None
    duration_seconds: int | None = None
    status: str
