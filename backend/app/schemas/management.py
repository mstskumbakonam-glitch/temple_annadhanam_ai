"""Temple, hall, seat-status, session, attendance and dashboard schemas."""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.models.enums import (
    DailyAttendanceStatus,
    MealType,
    SeatStatus,
    SeatStatusSource,
    SessionStatus,
)
from app.schemas.common import HallCode, NonEmptyName, SeatCode, StaffCode

TempleCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=32,
                      pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]
LongName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
OptionalText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]

_PHONE = re.compile(r"^\+?[0-9][0-9 \-]{5,19}$")


def _phone(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not _PHONE.fullmatch(value):
        raise ValueError("phone must be digits (optionally +, spaces or dashes), 6-20 characters")
    return value


# ---------------------------------------------------------------------------
# Seat counts (shared by temples, halls and the dashboard)
# ---------------------------------------------------------------------------
class SeatCounts(BaseModel):
    """Counting rules (also in docs/MANAGEMENT.md):

    * installed      = enabled seats (disabled seats are removed from service entirely)
    * capacity       = installed - out_of_service
    * available      = capacity - occupied - reserved
    * occupancy_pct  = occupied / capacity * 100 (0 when capacity is 0)
    Every seat has exactly one status, so no seat is ever counted twice.
    """

    installed: int = 0
    capacity: int = 0
    available: int = 0
    occupied: int = 0
    reserved: int = 0
    out_of_service: int = 0
    occupancy_percentage: float = 0.0


# ---------------------------------------------------------------------------
# Temples
# ---------------------------------------------------------------------------
class TempleBase(BaseModel):
    name: LongName
    address: Annotated[str, StringConstraints(strip_whitespace=True, max_length=512)] | None = None
    district: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    contact_phone: str | None = None
    active: bool = True

    @field_validator("contact_phone")
    @classmethod
    def _check_phone(cls, v: str | None) -> str | None:
        return _phone(v)


class TempleCreate(TempleBase):
    temple_code: TempleCode = Field(description="Short unique code, e.g. KMB-01.")


class TempleUpdate(BaseModel):
    name: LongName | None = None
    address: Annotated[str, StringConstraints(strip_whitespace=True, max_length=512)] | None = None
    district: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    contact_phone: str | None = None
    active: bool | None = None

    @field_validator("contact_phone")
    @classmethod
    def _check_phone(cls, v: str | None) -> str | None:
        return _phone(v)


class TempleRead(TempleBase):
    temple_code: str
    is_demo: bool
    hall_count: int
    seats: SeatCounts
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Halls
# ---------------------------------------------------------------------------
class HallBase(BaseModel):
    name: LongName
    building: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] | None = None
    floor: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None
    location_note: OptionalText | None = None
    active: bool = True


class HallCreate(HallBase):
    hall_code: HallCode = Field(description="Unique code, e.g. KMB-01-H1. Used by seats.")
    temple_code: TempleCode


class HallUpdate(BaseModel):
    name: LongName | None = None
    building: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] | None = None
    floor: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None
    location_note: OptionalText | None = None
    active: bool | None = None
    temple_code: TempleCode | None = Field(default=None, description="Move the hall to another temple.")


class HallRead(HallBase):
    hall_code: str
    temple_code: str
    temple_name: str
    is_demo: bool
    seats: SeatCounts
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Seats (confirmed status)
# ---------------------------------------------------------------------------
class HallSeatRead(BaseModel):
    seat_id: str
    hall_id: str
    seat_label: str | None = None
    row_number: int | None = None
    column_number: int | None = None
    enabled: bool
    status: SeatStatus
    status_source: SeatStatusSource
    status_since: datetime
    occupied_since: datetime | None = Field(default=None, description="Set only while OCCUPIED.")
    reservation_ref: str | None = None
    reserved_session_id: int | None = None
    status_version: int
    updated_at: datetime


class SeatStatusChange(BaseModel):
    status: SeatStatus
    expected_version: int | None = Field(
        default=None, ge=1,
        description="The status_version you saw. If someone changed the seat since, the "
        "request fails with 409 instead of overwriting their change.")
    reservation_ref: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] | None = None
    session_id: int | None = Field(default=None, ge=1, description="Session the reservation is for.")
    source: SeatStatusSource = SeatStatusSource.MANUAL
    note: OptionalText | None = None

    @model_validator(mode="after")
    def _reservation_fields(self) -> "SeatStatusChange":
        if self.status is not SeatStatus.RESERVED and (self.reservation_ref or self.session_id):
            raise ValueError("reservation_ref and session_id are only valid when reserving")
        return self


class SeatGenerate(BaseModel):
    rows: int = Field(ge=1, le=100)
    columns: int = Field(ge=1, le=100)
    prefix: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, max_length=8,
                                             pattern=r"^[A-Za-z]*$")] = "S"

    @model_validator(mode="after")
    def _limit(self) -> "SeatGenerate":
        if self.rows * self.columns > 2000:
            raise ValueError("at most 2000 seats per request")
        return self


class SeatGenerateResult(BaseModel):
    created: int
    skipped_existing: int
    hall_id: str


class SeatStatusEventRead(BaseModel):
    seat_id: str
    hall_id: str
    from_status: SeatStatus
    to_status: SeatStatus
    changed_at: datetime
    source: SeatStatusSource
    actor_role: str | None = None
    reservation_ref: str | None = None
    session_id: int | None = None
    previous_duration_seconds: int | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
class SessionBase(BaseModel):
    name: LongName
    meal_type: MealType
    session_date: date = Field(description="Local date in SITE_TIMEZONE (Asia/Kolkata).")
    start_time: time = Field(description="Local start time, e.g. 11:30.")
    end_time: time = Field(description="Local end time; must be after start (same day).")
    expected_devotees: int = Field(default=0, ge=0, le=1_000_000)
    responsible_staff_code: StaffCode | None = None
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None

    @model_validator(mode="after")
    def _times(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time (sessions cannot cross midnight)")
        return self


class SessionCreate(SessionBase):
    hall_code: HallCode


class SessionUpdate(BaseModel):
    name: LongName | None = None
    meal_type: MealType | None = None
    hall_code: HallCode | None = None
    session_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    expected_devotees: int | None = Field(default=None, ge=0, le=1_000_000)
    actual_devotees: int | None = Field(default=None, ge=0, le=1_000_000)
    responsible_staff_code: StaffCode | None = None
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    status: Literal["SCHEDULED", "IN_PROGRESS", "COMPLETED"] | None = Field(
        default=None, description="Use POST /cancel to cancel a session.")


class SessionCancel(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=255)]


class SessionRead(BaseModel):
    id: int
    name: str
    meal_type: MealType
    temple_code: str
    temple_name: str
    hall_code: str
    hall_name: str
    session_date: date
    start_time: time
    end_time: time
    starts_at: datetime
    ends_at: datetime
    expected_devotees: int
    actual_devotees: int | None
    hall_capacity: int = Field(description="Hall seat capacity now (enabled, in service).")
    capacity_warning: str | None = None
    status: SessionStatus
    timing: Literal["upcoming", "ongoing", "past"]
    responsible_staff_code: str | None = None
    responsible_staff_name: str | None = None
    notes: str | None = None
    cancel_reason: str | None = None
    is_demo: bool


# ---------------------------------------------------------------------------
# Daily attendance register
# ---------------------------------------------------------------------------
class AttendanceMark(BaseModel):
    staff_code: StaffCode
    attendance_date: date
    status: DailyAttendanceStatus
    check_in_at: datetime | None = None
    check_out_at: datetime | None = None
    note: OptionalText | None = None

    @model_validator(mode="after")
    def _order(self):
        if self.check_in_at and self.check_out_at and self.check_out_at < self.check_in_at:
            raise ValueError("check_out_at must be after check_in_at")
        return self


class AttendanceBulk(BaseModel):
    entries: list[AttendanceMark] = Field(min_length=1, max_length=500)


class DailyAttendanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    staff_code: str
    staff_name: str
    attendance_date: date
    status: DailyAttendanceStatus
    check_in_at: datetime | None = None
    check_out_at: datetime | None = None
    note: str | None = None
    source: str


# ---------------------------------------------------------------------------
# Dashboard overview
# ---------------------------------------------------------------------------
class StaffAttendanceSummary(BaseModel):
    in_use: bool = Field(description="False until any attendance has ever been recorded.")
    date: date
    present: int
    half_day: int
    absent: int
    on_leave: int
    not_marked: int


class SessionSummary(BaseModel):
    today_total: int
    today_scheduled: int
    today_in_progress: int
    today_completed: int
    today_cancelled: int
    upcoming: int = Field(description="Scheduled sessions that have not started yet (any date).")
    completed_total: int


class ManagementOverview(BaseModel):
    generated_at: datetime
    site_timezone: str
    local_date: date
    temple_code: str | None = Field(description="Scope filter, or null for all temples.")
    temples_total: int
    temples_active: int
    halls_total: int
    halls_active: int
    seats: SeatCounts
    reservations_enabled: bool = True
    staff_total_active: int
    attendance: StaffAttendanceSummary
    sessions: SessionSummary
    todays_sessions: list[SessionRead]
    active_alerts: int | None = Field(description="Open AI crowd alerts; null when scoped to a temple.")
    demo_records_present: bool


class AuthInfo(BaseModel):
    role: str
    auth_required: bool
    site_timezone: str
    demo_mode: bool
    app_version: str
    environment: str


class RegisterRow(BaseModel):
    """One staff member on a day's attendance register; status null = not marked yet."""

    staff_code: str
    staff_name: str
    designation: str | None = None
    shift: str | None = None
    temple_code: str | None = None
    hall_code: str | None = None
    status: DailyAttendanceStatus | None = None
    check_in_at: datetime | None = None
    check_out_at: datetime | None = None
    note: str | None = None


class AttendanceRegister(BaseModel):
    date: date
    rows: list[RegisterRow]
    summary: StaffAttendanceSummary
