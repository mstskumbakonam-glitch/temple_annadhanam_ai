"""FastAPI routers.

Routers are thin: they validate input, call a service and map the result onto a
response model. Query construction and transactions live in app/services.
"""

from app.api.ai import router as ai_router
from app.api.analytics import router as analytics_router
from app.api.cameras import router as cameras_router
from app.api.dashboard import router as dashboard_router
from app.api.errors import register_exception_handlers
from app.api.health import router as health_router
from app.api.seats import router as seats_router
from app.api.staff import router as staff_router
from app.api.visitors import router as visitors_router

# Registration order matters only within a router, not between routers.
ALL_ROUTERS = [
    health_router,
    dashboard_router,
    cameras_router,
    ai_router,
    analytics_router,
    visitors_router,
    staff_router,
    seats_router,
]

__all__ = [
    "ALL_ROUTERS",
    "register_exception_handlers",
    "health_router",
    "dashboard_router",
    "cameras_router",
    "ai_router",
    "analytics_router",
    "visitors_router",
    "staff_router",
    "seats_router",
]
