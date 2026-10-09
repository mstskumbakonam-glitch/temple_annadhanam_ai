"""Health endpoints: application liveness and database connectivity."""

from fastapi import APIRouter, Response, status

from app.security import RequireRole

from app.config import get_settings
from app.database import check_database_connection

router = APIRouter(prefix="/api/health", tags=["system"])
settings = get_settings()


@router.get("")
def health() -> dict:
    """Liveness check. Does not touch the database, so it stays up during an outage."""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "auth_required": settings.auth_enabled,
        "demo_mode": settings.demo_mode,
    }


@router.get("/db", dependencies=[RequireRole])
def database_health(response: Response) -> dict:
    """PostgreSQL connectivity check.

    Returns 200 when reachable and 503 when not. A database outage never raises
    out of this endpoint, so the rest of the API keeps serving.
    """
    result = check_database_connection()
    if result["status"] != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
