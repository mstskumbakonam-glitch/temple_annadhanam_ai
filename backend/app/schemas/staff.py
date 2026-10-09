"""Staff and attendance schemas.

face_embedding is deliberately absent from every schema here: enrollment and
matching belong to the face-recognition phase.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import AttendanceStatus, DailyAttendanceStatus, StaffShift
from app.schemas.common import CameraCode, HallCode, NonEmptyName, StaffCode


def _phone(value: str | None) -> str | None:
    from app.schemas.management import _phone as check

    return check(value)


class StaffBase(BaseModel):
    staff_name: NonEmptyName
    employee_code: str | None = Field(default=None, max_length=64)
    department: str | None = Field(default=None, max_length=128)
    designation: str | None = Field(default=None, max_length=80, description="Role, e.g. Cook, Supervisor.")
    shift: StaffShift | None = None
    phone: str | None = Field(default=None, description="Visible to admins only.")
    active: bool = True

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: str | None) -> str | None:
        return _phone(v)


class StaffCreate(StaffBase):
    """staff_code is omitted: PostgreSQL assigns STAFF-001 from a sequence."""

    temple_code: str | None = Field(default=None, max_length=32, description="Assigned temple.")
    hall_code: HallCode | None = Field(default=None, description="Assigned hall (must be in the temple).")


class StaffUpdate(BaseModel):
    staff_name: NonEmptyName | None = None
    employee_code: str | None = Field(default=None, max_length=64)
    department: str | None = Field(default=None, max_length=128)
    designation: str | None = Field(default=None, max_length=80)
    shift: StaffShift | None = None
    phone: str | None = None
    active: bool | None = None
    temple_code: str | None = Field(default=None, max_length=32)
    hall_code: HallCode | None = None

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: str | None) -> str | None:
        return _phone(v)


class StaffRead(StaffBase):
    model_config = ConfigDict(from_attributes=True)

    staff_code: StaffCode
    temple_code: str | None = None
    hall_code: str | None = None
    is_demo: bool = False
    today_attendance: DailyAttendanceStatus | None = Field(
        default=None, description="Today's register entry; null = not marked (not 'absent').")
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
