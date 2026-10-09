"""SQLAlchemy 2.x ORM models.

Every model is imported here so that Base.metadata is fully populated before
Alembic autogenerate compares it against the live database.
"""

from app.models.analytics import CrowdAlert, CrowdCountSnapshot
from app.models.base import Base, CreatedAtMixin, TimestampMixin, bigint_pk
from app.models.camera import Camera
from app.models.enums import (
    AlertSeverity,
    AlertType,
    AttendanceStatus,
    CameraEventType,
    CameraStatus,
    OccupancyStatus,
    PersonType,
    VisitorEventType,
    VisitorStatus,
)
from app.models.events import CameraEvent, VisitorEvent
from app.models.occupancy import SeatOccupancy
from app.models.seat import Seat
from app.models.sequences import (
    STAFF_CODE_SEQUENCE,
    VISITOR_CODE_SEQUENCE,
    staff_code_seq,
    visitor_code_seq,
)
from app.models.staff import Staff, StaffAttendance
from app.models.visitor import Visitor

__all__ = [
    "Base",
    "TimestampMixin",
    "CreatedAtMixin",
    "bigint_pk",
    "Camera",
    "Visitor",
    "Staff",
    "StaffAttendance",
    "Seat",
    "SeatOccupancy",
    "VisitorEvent",
    "CameraEvent",
    "CrowdAlert",
    "CrowdCountSnapshot",
    "AlertType",
    "AlertSeverity",
    "CameraStatus",
    "CameraEventType",
    "VisitorStatus",
    "VisitorEventType",
    "AttendanceStatus",
    "PersonType",
    "OccupancyStatus",
    "VISITOR_CODE_SEQUENCE",
    "STAFF_CODE_SEQUENCE",
    "visitor_code_seq",
    "staff_code_seq",
]
