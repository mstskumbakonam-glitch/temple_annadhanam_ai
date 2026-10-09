"""Enumerations shared by the ORM models.

These are stored as VARCHAR columns guarded by CHECK constraints rather than
native PostgreSQL ENUM types: adding a new value later is a cheap constraint
change instead of an ALTER TYPE that locks the table.
"""

from enum import StrEnum


class CameraStatus(StrEnum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    ERROR = "ERROR"
    DISABLED = "DISABLED"


class CameraEventType(StrEnum):
    # Connection health (Phase 3). These are the CAMERA_CONNECTED /
    # CAMERA_DISCONNECTED / STREAM_ERROR transitions of the capture worker.
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    ERROR = "ERROR"
    RECOVERED = "RECOVERED"
    # AI pipeline lifecycle (Phase 5): the camera itself may be perfectly fine,
    # so these cannot be expressed as connection states.
    AI_STARTED = "AI_STARTED"
    AI_STOPPED = "AI_STOPPED"


class VisitorStatus(StrEnum):
    ACTIVE = "ACTIVE"      # detected and being tracked
    INSIDE = "INSIDE"      # confirmed inside the hall
    EXITED = "EXITED"      # session closed


class VisitorEventType(StrEnum):
    DETECTED = "DETECTED"
    ENTERED = "ENTERED"
    EXITED = "EXITED"
    SEAT_ASSIGNED = "SEAT_ASSIGNED"
    SEAT_RELEASED = "SEAT_RELEASED"
    LOST = "LOST"


class AttendanceStatus(StrEnum):
    PRESENT = "PRESENT"
    EXITED = "EXITED"


class PersonType(StrEnum):
    VISITOR = "VISITOR"
    STAFF = "STAFF"


class OccupancyStatus(StrEnum):
    OCCUPIED = "OCCUPIED"
    RELEASED = "RELEASED"


class SeatStatus(StrEnum):
    """Confirmed status of a seat. A seat is in exactly one status at a time, so
    available + occupied + reserved (+ out of service) can never double count."""

    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    RESERVED = "RESERVED"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


class SeatStatusSource(StrEnum):
    MANUAL = "MANUAL"              # set by an operator
    AI_CONFIRMED = "AI_CONFIRMED"  # an AI suggestion that an operator confirmed


class SessionStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class MealType(StrEnum):
    BREAKFAST = "BREAKFAST"
    LUNCH = "LUNCH"
    DINNER = "DINNER"
    PRASADAM = "PRASADAM"
    SPECIAL = "SPECIAL"


class StaffShift(StrEnum):
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"
    EVENING = "EVENING"
    NIGHT = "NIGHT"
    FULL_DAY = "FULL_DAY"


class DailyAttendanceStatus(StrEnum):
    PRESENT = "PRESENT"
    HALF_DAY = "HALF_DAY"
    ABSENT = "ABSENT"
    LEAVE = "LEAVE"


class AlertType(StrEnum):
    CROWD_DENSITY = "CROWD_DENSITY"        # a zone stayed at/above the alert density level
    QUEUE_CONGESTION = "QUEUE_CONGESTION"  # a queue zone stayed at/above its length limit


class AlertSeverity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


def values(enum_cls) -> list[str]:
    """Member values, for building CHECK constraints."""
    return [member.value for member in enum_cls]
