"""Crowd analytics API schemas. Anonymous by construction: counts, zones, alerts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.ai.analytics_config import DensityLevel


class LineLive(BaseModel):
    line_id: str
    name: str
    entries: int
    exits: int


class ZoneLive(BaseModel):
    zone_id: str
    name: str
    kind: str
    count: int
    smoothed_count: float
    density: float
    density_unit: str
    level: DensityLevel
    queue_length: int | None = None
    avg_wait_seconds: float | None = None
    estimated_wait_seconds: float | None = None
    throughput_per_minute: float | None = None


class AlertLive(BaseModel):
    alert_id: str
    alert_type: str
    zone_id: str
    severity: str
    started_at: datetime
    value: float
    threshold: float
    message: str


class CameraLive(BaseModel):
    camera_id: str
    camera_name: str
    location: str | None = None
    enabled: bool
    source_kind: str = Field(description="'live' camera stream or 'recorded' demo video.")
    connection_state: str
    connected: bool
    ai_running: bool
    stale: bool = Field(description="Running, but no new frame for longer than CAMERA_READ_TIMEOUT.")
    person_count: int | None = Field(description="Tracks visible in the last frame; null = unknown.")
    confirmed_count: int | None = Field(
        description="Tracks confirmed over several frames (used by zones and lines); null = unknown.")
    entries_today: int
    exits_today: int
    lines: list[LineLive] = []
    zones: list[ZoneLive] = []
    alerts: list[AlertLive] = []
    processing_fps: float | None = None
    capture_fps: float | None = None
    inference_ms: float | None = None
    last_frame_timestamp: datetime | None = None
    reconnect_count: int = 0
    last_error: str | None = None
    preview_available: bool = False
    has_analytics: bool = False


class LiveOverview(BaseModel):
    generated_at: datetime
    site_timezone: str
    mode: str = Field(description="'live', 'demo' (recorded video only), 'mixed' or 'idle'.")
    demo_mode: bool
    ai_state: str
    ai_detail: str | None = None
    model_name: str | None = None
    device: str | None = None
    cameras_total: int
    cameras_online: int
    people_now: int | None = Field(description="Sum of confirmed counts over cameras with data.")
    entries_today: int
    exits_today: int
    open_alerts: int
    cameras: list[CameraLive]


class HistoryPoint(BaseModel):
    bucket_start: datetime
    avg_count: float
    max_count: int
    entries: int
    exits: int


class HistoryResponse(BaseModel):
    camera_id: str | None
    zone_id: str
    minutes: int
    bucket_minutes: int
    points: list[HistoryPoint]


class AlertRead(BaseModel):
    alert_id: str
    camera_id: str
    zone_id: str | None
    alert_type: str
    severity: str
    active: bool
    started_at: datetime
    ended_at: datetime | None
    peak_value: float | None
    threshold: float | None
    message: str | None
    acknowledged_at: datetime | None
