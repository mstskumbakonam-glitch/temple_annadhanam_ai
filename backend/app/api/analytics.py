"""Crowd analytics endpoints: live overview, history, alerts, per-camera
line/zone configuration and the annotated preview frame."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.ai.analytics_config import AnalyticsConfig, DensityLevel, parse_analytics_config
from app.api.deps import PaginationParams, get_db
from app.api.errors import HTTP_422
from app.camera.state import runtime_registry
from app.config import get_settings
from app.schemas.analytics import AlertRead, HistoryResponse, LiveOverview
from app.schemas.common import CameraCode, ErrorResponse, Page
from app.security import RequireRole, require_admin
from app.services import analytics_service, camera_service

router = APIRouter(tags=["Crowd Analytics"], dependencies=[RequireRole])

DbSession = Annotated[Session, Depends(get_db)]
NOT_FOUND = {404: {"model": ErrorResponse, "description": "Not found"}}


@router.get(
    "/api/analytics/live",
    response_model=LiveOverview,
    summary="Live crowd overview",
    description="Everything the dashboard shows, in one call: per-camera counts, entries and "
    "exits today, zone density, queues, open alerts, FPS and latency. `source_kind` says "
    "whether each camera is a live stream or a recorded demo video; `mode` summarises it. "
    "Unknown values are null, never a made-up zero.",
)
def live(session: DbSession) -> LiveOverview:
    return analytics_service.live_overview(session, get_settings())


@router.get(
    "/api/analytics/history",
    response_model=HistoryResponse,
    responses=NOT_FOUND,
    summary="Count history",
    description="People counts per time bucket from the stored per-minute history. Without "
    "camera_id the cameras are summed. zone_id '*' is the whole camera view.",
)
def history(
    session: DbSession,
    camera_id: CameraCode | None = Query(None),
    zone_id: str = Query("*", max_length=32, pattern=r"^(\*|[A-Za-z0-9][A-Za-z0-9_-]*)$"),
    minutes: int = Query(180, ge=5, le=60 * 24 * 31),
    bucket_minutes: int = Query(5),
) -> HistoryResponse:
    if bucket_minutes not in analytics_service.ALLOWED_BUCKETS:
        raise HTTPException(
            HTTP_422, detail=f"bucket_minutes must be one of {analytics_service.ALLOWED_BUCKETS}")
    return analytics_service.history(session, camera_id=camera_id, zone_id=zone_id,
                                     minutes=minutes, bucket_minutes=bucket_minutes)


@router.get("/api/alerts", response_model=Page[AlertRead], summary="Crowd alerts")
def alerts(
    session: DbSession,
    pagination: PaginationParams,
    active: bool | None = Query(None, description="true = still open, false = cleared"),
    camera_id: CameraCode | None = Query(None),
) -> Page[AlertRead]:
    items, result = analytics_service.list_alerts(
        session, page=pagination.page, page_size=pagination.page_size,
        active=active, camera_id=camera_id)
    return Page[AlertRead](items=items, page=result.page, page_size=result.page_size,
                           total=result.total)


@router.post("/api/alerts/{alert_id}/acknowledge", response_model=AlertRead, responses=NOT_FOUND,
             summary="Acknowledge an alert (admin)")
def acknowledge(
    alert_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")],
    session: DbSession,
) -> AlertRead:
    return analytics_service.acknowledge_alert(session, alert_id)


@router.get("/api/cameras/{camera_id}/analytics", response_model=AnalyticsConfig,
            responses=NOT_FOUND, summary="Lines and zones of a camera")
def get_config(camera_id: CameraCode, session: DbSession) -> AnalyticsConfig:
    return analytics_service.get_analytics_config(session, camera_id)


@router.put(
    "/api/cameras/{camera_id}/analytics",
    response_model=AnalyticsConfig,
    responses=NOT_FOUND,
    dependencies=[Depends(require_admin)],
    summary="Set lines and zones of a camera (admin)",
    description="Coordinates are fractions of the frame (0..1). The running worker picks the "
    "change up within AI_SYNC_INTERVAL seconds and restarts that camera's tracking.",
)
def put_config(camera_id: CameraCode, config: AnalyticsConfig, session: DbSession) -> AnalyticsConfig:
    return analytics_service.set_analytics_config(session, camera_id, config)


@router.get(
    "/api/cameras/{camera_id}/preview.jpg",
    responses={**NOT_FOUND, 200: {"content": {"image/jpeg": {}}}},
    response_class=Response,
    summary="Annotated last processed frame",
    description="Requires PREVIEW_ENABLED=true. Heads are pixelated unless PREVIEW_ANONYMIZE=false.",
)
def preview(camera_id: CameraCode, session: DbSession) -> Response:
    settings = get_settings()
    if not settings.preview_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Preview is disabled (PREVIEW_ENABLED=false).")
    camera = camera_service.get_by_code(session, camera_id)
    runtime = runtime_registry.get(camera.camera_id)
    held = runtime.preview() if runtime else None
    if held is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No processed frame available yet.")
    frame, tracks, when = held
    from app.services.preview import render_preview

    try:
        config = parse_analytics_config(camera.analytics_config)
    except Exception:
        config = AnalyticsConfig()
    analytics = runtime.snapshot().analytics
    levels: dict[str, DensityLevel] = (
        {z.zone_id: z.level for z in analytics.zones} if analytics else {})
    jpeg = render_preview(frame, tracks, config=config, zone_levels=levels,
                          recorded=camera.source_kind == "recorded", timestamp=when,
                          anonymize=settings.preview_anonymize,
                          max_width=settings.preview_max_width)
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})

