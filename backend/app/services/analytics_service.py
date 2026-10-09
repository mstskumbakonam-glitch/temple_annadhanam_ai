"""Crowd analytics read models: live overview, today's totals, history, alerts.

Live values come from the running workers (app.camera.state); totals and
history come from PostgreSQL. Nothing is invented: a camera without data
reports null, not zero.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from app.ai.analytics_config import AnalyticsConfig
from app.camera.state import (
    Connection,
    ManagerStatusHolder,
    RuntimeRegistry,
    manager_status,
    runtime_registry,
)
from app.config import Settings
from app.models import Camera, CrowdAlert, CrowdCountSnapshot, VisitorEvent
from app.models.enums import VisitorEventType
from app.schemas.analytics import (
    AlertLive,
    AlertRead,
    CameraLive,
    HistoryPoint,
    HistoryResponse,
    LineLive,
    LiveOverview,
    ZoneLive,
)
from app.services import camera_service
from app.services.exceptions import NotFoundError
from app.services.pagination import PageResult, paginate
from app.utils.logs import redact_text
from app.utils.time import utc_now

ALLOWED_BUCKETS = (1, 5, 15, 60)


def site_day_bounds(tz_name: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Start and end (UTC) of 'today' in the temple's own time zone."""
    tz = ZoneInfo(tz_name)
    local = (now or utc_now()).astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def crossings_today(session: Session, settings: Settings) -> dict[int, tuple[int, int]]:
    """{cameras.id: (entries, exits)} from persisted line-crossing events."""
    start, end = site_day_bounds(settings.site_timezone)
    rows = session.execute(
        select(VisitorEvent.camera_id, VisitorEvent.event_type, func.count())
        .where(
            VisitorEvent.event_time >= start,
            VisitorEvent.event_time < end,
            VisitorEvent.event_type.in_([VisitorEventType.ENTERED, VisitorEventType.EXITED]),
            VisitorEvent.visitor_id.is_(None),
            VisitorEvent.event_metadata["source"].astext == "line_crossing",
        )
        .group_by(VisitorEvent.camera_id, VisitorEvent.event_type)
    ).all()
    totals: dict[int, list[int]] = {}
    for camera_id, event_type, count in rows:
        slot = totals.setdefault(camera_id, [0, 0])
        slot[0 if event_type == VisitorEventType.ENTERED else 1] += count
    return {k: (v[0], v[1]) for k, v in totals.items()}


def open_alert_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(CrowdAlert)
                          .where(CrowdAlert.ended_at.is_(None))) or 0


def _camera_live(camera: Camera, registry: RuntimeRegistry, settings: Settings,
                 today: tuple[int, int]) -> CameraLive:
    runtime = registry.get(camera.camera_id)
    snap = registry.snapshot(camera.camera_id)
    analytics = snap.analytics
    running = snap.ai_running
    stale = bool(
        running and snap.connected and snap.last_frame_timestamp is not None
        and (utc_now() - snap.last_frame_timestamp).total_seconds() > settings.camera_read_timeout
    )
    return CameraLive(
        camera_id=camera.camera_id,
        camera_name=camera.camera_name,
        location=camera.location,
        enabled=camera.enabled,
        source_kind=camera.source_kind,
        connection_state=snap.connection_state.value,
        connected=snap.connected,
        ai_running=running,
        stale=stale,
        person_count=snap.person_count,
        confirmed_count=(analytics.confirmed_count
                         if analytics is not None and snap.connected and snap.person_count is not None
                         else None),
        entries_today=today[0],
        exits_today=today[1],
        lines=[LineLive(**asdict(line)) for line in analytics.lines] if analytics else [],
        zones=[ZoneLive(**asdict(zone)) for zone in analytics.zones] if analytics else [],
        alerts=[AlertLive(**asdict(alert)) for alert in analytics.alerts] if analytics else [],
        processing_fps=round(snap.processing_fps, 2) if snap.processing_fps is not None else None,
        capture_fps=round(snap.capture_fps, 2) if snap.capture_fps is not None else None,
        inference_ms=snap.inference_ms,
        last_frame_timestamp=snap.last_frame_timestamp,
        reconnect_count=snap.reconnect_count,
        last_error=redact_text(snap.last_error) if snap.last_error else None,
        preview_available=bool(settings.preview_enabled and runtime is not None
                               and runtime.preview() is not None),
        has_analytics=camera.has_analytics,
        warnings=_config_warnings(camera, settings),
    )


def _config_warnings(camera: Camera, settings: Settings) -> list[str]:
    try:
        config = AnalyticsConfig.model_validate(camera.analytics_config or {})
    except Exception:
        return ["Stored analytics configuration is invalid; lines and zones are disabled."]
    fps = config.tuning.process_fps or settings.ai_process_fps
    return config.warnings(fps)


def live_overview(session: Session, settings: Settings,
                  registry: RuntimeRegistry = runtime_registry,
                  manager: ManagerStatusHolder = manager_status) -> LiveOverview:
    cameras = camera_service.list_status(session)
    today = crossings_today(session, settings)
    live = [_camera_live(c, registry, settings, today.get(c.id, (0, 0))) for c in cameras]

    running = [c for c in live if c.ai_running]
    kinds = {c.source_kind for c in running}
    mode = "idle" if not running else ("demo" if kinds == {"recorded"}
                                       else "live" if kinds == {"live"} else "mixed")
    known = [c.confirmed_count for c in live if c.confirmed_count is not None]
    status = manager.get()
    return LiveOverview(
        generated_at=utc_now(),
        site_timezone=settings.site_timezone,
        mode=mode,
        demo_mode=settings.demo_mode,
        ai_state=status.state.value,
        ai_detail=redact_text(status.detail) if status.detail else None,
        model_name=status.model_name,
        device=status.device,
        cameras_total=len(live),
        cameras_online=sum(1 for c in live if c.connection_state == Connection.ONLINE.value),
        people_now=sum(known) if known else None,
        entries_today=sum(c.entries_today for c in live),
        exits_today=sum(c.exits_today for c in live),
        open_alerts=open_alert_count(session),
        cameras=live,
    )


def history(session: Session, *, camera_id: str | None, zone_id: str, minutes: int,
            bucket_minutes: int) -> HistoryResponse:
    """Per-bucket people counts. Without camera_id, cameras are summed (zone '*')."""
    if bucket_minutes not in ALLOWED_BUCKETS:
        raise ValueError(f"bucket_minutes must be one of {ALLOWED_BUCKETS}")
    since = utc_now() - timedelta(minutes=minutes)
    filters = [CrowdCountSnapshot.bucket_start >= since, CrowdCountSnapshot.zone_id == zone_id]
    if camera_id is not None:
        camera = camera_service.get_by_code(session, camera_id)
        filters.append(CrowdCountSnapshot.camera_id == camera.id)

    bucket = func.date_bin(
        text(f"interval '{int(bucket_minutes)} minutes'"),
        CrowdCountSnapshot.bucket_start,
        text("TIMESTAMPTZ '2000-01-01 00:00:00+00'"),
    ).label("bucket")
    # Per camera first (weighted mean over its minutes), then summed over cameras,
    # so "total people" is the sum of what each camera saw, not an average of them.
    per_camera = (
        select(
            bucket,
            CrowdCountSnapshot.camera_id,
            (func.sum(CrowdCountSnapshot.avg_count * CrowdCountSnapshot.samples)
             / func.sum(CrowdCountSnapshot.samples)).label("avg_count"),
            func.max(CrowdCountSnapshot.max_count).label("max_count"),
            func.sum(CrowdCountSnapshot.entries).label("entries"),
            func.sum(CrowdCountSnapshot.exits).label("exits"),
        )
        .where(and_(*filters))
        .group_by(bucket, CrowdCountSnapshot.camera_id)
        .subquery()
    )
    rows = session.execute(
        select(
            per_camera.c.bucket,
            func.sum(per_camera.c.avg_count),
            func.sum(per_camera.c.max_count),
            func.sum(per_camera.c.entries),
            func.sum(per_camera.c.exits),
        )
        .group_by(per_camera.c.bucket)
        .order_by(per_camera.c.bucket)
    ).all()
    return HistoryResponse(
        camera_id=camera_id, zone_id=zone_id, minutes=minutes, bucket_minutes=bucket_minutes,
        points=[HistoryPoint(bucket_start=b, avg_count=round(float(a), 2), max_count=int(m),
                             entries=int(e), exits=int(x)) for b, a, m, e, x in rows],
    )


def _alert_read(alert: CrowdAlert) -> AlertRead:
    return AlertRead(
        alert_id=alert.alert_uid, camera_id=alert.camera.camera_id, zone_id=alert.zone_id,
        alert_type=alert.alert_type, severity=alert.severity, active=alert.ended_at is None,
        started_at=alert.started_at, ended_at=alert.ended_at, peak_value=alert.peak_value,
        threshold=alert.threshold, message=alert.message, acknowledged_at=alert.acknowledged_at,
    )


def list_alerts(session: Session, *, page: int, page_size: int, active: bool | None,
                camera_id: str | None) -> tuple[list[AlertRead], PageResult]:
    statement = select(CrowdAlert).order_by(CrowdAlert.started_at.desc(), CrowdAlert.id.desc())
    if active is True:
        statement = statement.where(CrowdAlert.ended_at.is_(None))
    elif active is False:
        statement = statement.where(CrowdAlert.ended_at.is_not(None))
    if camera_id is not None:
        camera = camera_service.get_by_code(session, camera_id)
        statement = statement.where(CrowdAlert.camera_id == camera.id)
    result = paginate(session, statement, page, page_size)
    return [_alert_read(a) for a in result.items], result


def acknowledge_alert(session: Session, alert_id: str) -> AlertRead:
    alert = session.scalar(select(CrowdAlert).where(CrowdAlert.alert_uid == alert_id))
    if alert is None:
        raise NotFoundError(f"Alert '{alert_id}' not found.")
    if alert.acknowledged_at is None:
        alert.acknowledged_at = utc_now()
        session.commit()
        session.refresh(alert)
    return _alert_read(alert)


def get_analytics_config(session: Session, camera_id: str) -> AnalyticsConfig:
    camera = camera_service.get_by_code(session, camera_id)
    return AnalyticsConfig.model_validate(camera.analytics_config or {})


def set_analytics_config(session: Session, camera_id: str, config: AnalyticsConfig) -> AnalyticsConfig:
    """Store the config. The camera manager restarts that camera's worker on its
    next sync (AI_SYNC_INTERVAL) so the new lines and zones take effect."""
    camera = camera_service.get_by_code(session, camera_id)
    camera.analytics_config = None if config == AnalyticsConfig() else config.model_dump(mode="json")
    session.commit()
    return config
