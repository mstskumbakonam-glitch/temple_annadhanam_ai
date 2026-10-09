"""AI runtime status endpoints. Read-only: they report, they do not control."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.security import RequireRole
from app.api.deps import get_db
from app.config import get_settings
from app.schemas.ai import AIRuntimeStatus
from app.services import ai_status_service

router = APIRouter(prefix="/api/ai", tags=["AI Runtime"], dependencies=[RequireRole])

DbSession = Annotated[Session, Depends(get_db)]


@router.get(
    "/status",
    response_model=AIRuntimeStatus,
    summary="AI pipeline status",
    description="Live state of the AI pipeline and of every configured camera. Values are "
    "read from the running workers; if AI is not running this says so, and counts are null "
    "rather than zero. RTSP URLs are never returned.",
)
def ai_status(session: DbSession) -> AIRuntimeStatus:
    return ai_status_service.get_runtime_status(session, get_settings())
