"""Staff and attendance schemas.

face_embedding is deliberately absent from every schema here: enrollment and
matching belong to the face-recognition phase.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AttendanceStatus
from app.schemas.common import CameraCode, NonEmptyName, StaffCode


class StaffBase(BaseModel):
    staff_name: NonEmptyName
    employee_code: str | None = Field(default=None, max_length=64)
    department: str | None = Field(default=None, max_length=128)
    active: bool = True


class StaffCreate(StaffBase):
    """staff_code is omitted: PostgreSQL assigns STAFF-001 from a sequence."""


class StaffUpdate(BaseModel):
    staff_name: NonEmptyName | None = None
    employee_code: str | None = Field(default=None, max_length=64)
    department: str | None = Field(default=None, max_length=128)
    active: bool | None = None


class StaffRead(StaffBase):
    model_config = ConfigDict(from_attributes=True)

    staff_code: StaffCode
    created_at: datetime
    updated_at: datetime


class AttendanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    staff_code: StaffCode
    staff_name: str
    camera_id: CameraCode | None = None
    entry_time: datetime
    exit_time: datetime | None = None
    last_seen: datetime | None = None
    status: AttendanceStatus
