"""Business logic layer.

Routes call these modules; they never build queries themselves. Services own
transactions (commit / rollback / refresh) and raise ServiceError subclasses,
which app/api/errors.py maps to HTTP responses.
"""

from app.services import (
    camera_service,
    dashboard_service,
    event_service,
    seat_service,
    staff_service,
    visitor_service,
)
from app.services.exceptions import (
    ConflictError,
    NotFoundError,
    ServiceError,
    ValidationError,
)

__all__ = [
    "camera_service",
    "visitor_service",
    "staff_service",
    "seat_service",
    "event_service",
    "dashboard_service",
    "ServiceError",
    "NotFoundError",
    "ConflictError",
    "ValidationError",
]
