"""Pydantic request/response schemas.

Routes always return these models, never raw SQLAlchemy objects.
"""

from app.schemas.camera import (
    CameraCreate,
    CameraRead,
    CameraStatusRead,
    CameraUpdate,
)
from app.schemas.common import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ErrorResponse,
    Page,
)
from app.schemas.dashboard import DashboardSummary, HallOccupancy
from app.schemas.event import CameraEventRead, VisitorEventRead
from app.schemas.seat import (
    PolygonPoint,
    SeatCreate,
    SeatHistoryRead,
    SeatRead,
    SeatStatusRead,
    SeatUpdate,
)
from app.schemas.staff import (
    AttendanceRead,
    StaffCreate,
    StaffRead,
    StaffUpdate,
)
from app.schemas.visitor import VisitorRead

__all__ = [
    "Page",
    "ErrorResponse",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "CameraCreate",
    "CameraUpdate",
    "CameraRead",
    "CameraStatusRead",
    "VisitorRead",
    "StaffCreate",
    "StaffUpdate",
    "StaffRead",
    "AttendanceRead",
    "SeatCreate",
    "SeatUpdate",
    "SeatRead",
    "SeatStatusRead",
    "SeatHistoryRead",
    "PolygonPoint",
    "VisitorEventRead",
    "CameraEventRead",
    "DashboardSummary",
    "HallOccupancy",
]
