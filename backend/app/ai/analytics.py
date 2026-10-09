"""Crowd analytics on top of anonymous tracks: entry/exit lines, zone density,
queue length and wait, and debounced alerts.

Runs as a FrameProcessor inside one camera's pipeline (single thread, no locks).
It never sees an identity: only temporary ByteTrack ids and box positions.

Design choices, in plain words
* A person's position is the bottom-centre of their box (their feet). On an
  angled CCTV view the feet are where the person actually stands; the box centre
  drifts with height and posture.
* Only CONFIRMED tracks count (matched in >= min_track_hits frames), so a
  detector glitch that lasts one frame never becomes an entry or a person.
* A line crossing needs a clear side change (outside a small hysteresis band)
  AND the movement must pass through the drawn line segment. Someone standing on
  the line, or walking around its end, is not counted.
* Zone counts are smoothed with a rolling median over a few seconds, and alerts
  are raised only after a condition has held for `raise_after_seconds` and
  cleared only after it has been absent for `clear_after_seconds`. One crowded
  moment produces one alert.

Known limits (documented, not hidden): if the tracker loses a person and gives
them a new id on the other side of a line, that crossing is missed; long
occlusions in dense queues shorten measured dwell times.
"""

from __future__ import annotations

import math
import statistics
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from app.ai.analytics_config import AnalyticsConfig, CountingLine, DensityLevel, Zone
from app.ai.pipeline_types import PipelineResult
from app.ai.types import TrackedObject
from app.models.enums import VisitorEventType
from app.services.runtime_events import (
    AlertRecord,
    CountSnapshotRecord,
    TrackEventRecord,
)

WHOLE_FRAME = "*"   # zone_id used for camera-wide count snapshots

Pixel = tuple[float, float]


# ---------------------------------------------------------------------------
# Geometry helpers (pure functions, unit-tested)
# ---------------------------------------------------------------------------
def signed_distance(point: Pixel, start: Pixel, end: Pixel) -> float:
    """Signed perpendicular distance from `point` to the infinite line start->end.

    Positive on side B (the side the normal (-dy, dx) points to), negative on side A.
    """
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return 0.0
    return (dx * (point[1] - start[1]) - dy * (point[0] - start[0])) / length


def segments_intersect(p1: Pixel, p2: Pixel, q1: Pixel, q2: Pixel, slack: float = 0.0) -> bool:
    """True if segment p1-p2 crosses segment q1-q2. `slack` (pixels) extends q at
    both ends so a crossing right at a line's end point is not lost to rounding."""
    if slack:
        qx, qy = q2[0] - q1[0], q2[1] - q1[1]
        n = math.hypot(qx, qy) or 1.0
        ux, uy = qx / n * slack, qy / n * slack
        q1, q2 = (q1[0] - ux, q1[1] - uy), (q2[0] + ux, q2[1] + uy)

    def orient(a: Pixel, b: Pixel, c: Pixel) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1, d2 = orient(q1, q2, p1), orient(q1, q2, p2)
    d3, d4 = orient(p1, p2, q1), orient(p1, p2, q2)
    return (d1 * d2 <= 0) and (d3 * d4 <= 0) and not (d1 == d2 == 0)


def point_in_polygon(point: Pixel, polygon: list[Pixel]) -> bool:
    """Ray casting. Points exactly on an edge may fall either way."""
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def density_level(value: float, medium: float, high: float, critical: float) -> DensityLevel:
    if value >= critical:
        return DensityLevel.CRITICAL
    if value >= high:
        return DensityLevel.HIGH
    if value >= medium:
        return DensityLevel.MEDIUM
    return DensityLevel.LOW


# ---------------------------------------------------------------------------
# Live state published to the API (immutable snapshots)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LineState:
    line_id: str
    name: str
    entries: int
    exits: int


@dataclass(frozen=True)
class ZoneState:
    zone_id: str
    name: str
    kind: str
    count: int                    # confirmed people inside now (raw)
    smoothed_count: float         # rolling median used for levels and alerts
    density: float                # smoothed_count, or per m^2 when area_m2 is known
    density_unit: str             # "people" or "people_per_m2"
    level: DensityLevel
    queue_length: int | None = None
    avg_wait_seconds: float | None = None       # mean dwell of people who left recently
    estimated_wait_seconds: float | None = None  # Little's law: length / throughput
    throughput_per_minute: float | None = None


@dataclass(frozen=True)
class ActiveAlert:
    alert_id: str
    alert_type: str
    zone_id: str
    severity: str
    started_at: datetime
    value: float
    threshold: float
    message: str


@dataclass(frozen=True)
class AnalyticsSnapshot:
    confirmed_count: int
    entries: int                  # since this pipeline started (all lines)
    exits: int
    lines: tuple[LineState, ...]
    zones: tuple[ZoneState, ...]
    alerts: tuple[ActiveAlert, ...]
    updated_at: datetime


# ---------------------------------------------------------------------------
# Internal per-feature state
# ---------------------------------------------------------------------------
@dataclass
class _TrackSide:
    side: int            # -1 (side A) or +1 (side B)
    point: Pixel
    last_counted: dict[str, datetime] = field(default_factory=dict)  # direction -> time


class _LineCounter:
    def __init__(self, line: CountingLine) -> None:
        self.line = line
        self.entries = 0
        self.exits = 0
        self._tracks: dict[tuple[str, int], _TrackSide] = {}
        self._frame: tuple[int, int] | None = None
        self._start: Pixel = (0.0, 0.0)
        self._end: Pixel = (0.0, 0.0)
        self._band = 0.0

    def _geometry(self, width: int, height: int) -> None:
        if self._frame == (width, height):
            return
        self._frame = (width, height)
        self._start = (self.line.start[0] * width, self.line.start[1] * height)
        self._end = (self.line.end[0] * width, self.line.end[1] * height)
        self._band = self.line.hysteresis * math.hypot(width, height)

    def update(self, tracks: list[TrackedObject], width: int, height: int,
               cooldown: float) -> list[tuple[TrackedObject, str]]:
        """Return (track, 'IN'|'OUT') for every crossing in this frame."""
        self._geometry(width, height)
        crossings = []
        for track in tracks:
            key = (track.tracker_session, track.track_id)
            anchor = track.bottom_center
            d = signed_distance(anchor, self._start, self._end)
            side = 1 if d > self._band else (-1 if d < -self._band else 0)
            state = self._tracks.get(key)
            if side == 0:
                continue                      # inside the band: no decision yet
            if state is None:
                self._tracks[key] = _TrackSide(side, anchor)
                continue
            if side != state.side and segments_intersect(
                state.point, anchor, self._start, self._end, slack=self._band
            ):
                a_to_b = state.side < 0 < side
                entering = a_to_b != self.line.invert
                direction = "IN" if entering else "OUT"
                last = state.last_counted.get(direction)
                if last is None or (track.timestamp - last).total_seconds() >= cooldown:
                    state.last_counted[direction] = track.timestamp
                    if entering:
                        self.entries += 1
                    else:
                        self.exits += 1
                    crossings.append((track, direction))
            state.side, state.point = side, anchor
        return crossings

    def forget(self, key: tuple[str, int]) -> None:
        self._tracks.pop(key, None)

    def reset(self) -> None:
        self._tracks.clear()


class _AlertState:
    """Debounce one boolean condition into raise / clear transitions."""

    def __init__(self) -> None:
        self.true_since: datetime | None = None
        self.false_since: datetime | None = None
        self.active: ActiveAlert | None = None
        self.peak = 0.0


class _ZoneMonitor:
    def __init__(self, zone: Zone, smoothing_seconds: float) -> None:
        self.zone = zone
        self._smoothing = timedelta(seconds=smoothing_seconds)
        self._samples: deque[tuple[datetime, int]] = deque()
        self._frame: tuple[int, int] | None = None
        self._polygon: list[Pixel] = []
        # queue bookkeeping
        self._entered: dict[tuple[str, int], datetime] = {}
        self._exits: deque[tuple[datetime, float]] = deque()   # (left_at, dwell seconds)
        self.state: ZoneState | None = None
        self.density_alert = _AlertState()
        self.queue_alert = _AlertState()

    def _geometry(self, width: int, height: int) -> None:
        if self._frame != (width, height):
            self._frame = (width, height)
            self._polygon = [(x * width, y * height) for x, y in self.zone.polygon]

    def update(self, tracks: list[TrackedObject], width: int, height: int,
               now: datetime) -> ZoneState:
        self._geometry(width, height)
        inside = [t for t in tracks if point_in_polygon(t.bottom_center, self._polygon)]
        count = len(inside)

        self._samples.append((now, count))
        while self._samples and now - self._samples[0][0] > self._smoothing:
            self._samples.popleft()
        smoothed = float(statistics.median(c for _, c in self._samples))

        z = self.zone
        if z.thresholds.per_square_metre and z.area_m2:
            density, unit = smoothed / z.area_m2, "people_per_m2"
        else:
            density, unit = smoothed, "people"
        level = density_level(density, z.thresholds.medium, z.thresholds.high, z.thresholds.critical)

        queue_fields: dict[str, Any] = {}
        if z.kind == "queue":
            queue_fields = self._queue_update(inside, now, count)

        self.state = ZoneState(
            zone_id=z.id, name=z.name, kind=z.kind, count=count,
            smoothed_count=smoothed, density=round(density, 3), density_unit=unit,
            level=level, **queue_fields,
        )
        return self.state

    def _queue_update(self, inside: list[TrackedObject], now: datetime, count: int) -> dict[str, Any]:
        present = {(t.tracker_session, t.track_id) for t in inside}
        for key in present:
            self._entered.setdefault(key, now)
        # Visible tracks that are no longer inside have left the queue.
        for key in [k for k in self._entered if k not in present]:
            self._record_exit(key, now)
        horizon = now - timedelta(minutes=10)
        while self._exits and self._exits[0][0] < horizon:
            self._exits.popleft()

        recent = [d for t, d in self._exits if t >= now - timedelta(minutes=5)]
        throughput = len(recent) / 5.0 if self._exits else None   # people per minute
        avg_wait = statistics.fmean(d for _, d in self._exits) if self._exits else None
        estimated = (count / throughput * 60.0) if throughput else None
        return {
            "queue_length": count,
            "avg_wait_seconds": round(avg_wait, 1) if avg_wait is not None else None,
            "estimated_wait_seconds": round(estimated, 1) if estimated is not None else None,
            "throughput_per_minute": round(throughput, 2) if throughput is not None else None,
        }

    def _record_exit(self, key: tuple[str, int], now: datetime) -> None:
        entered = self._entered.pop(key, None)
        if entered is None:
            return
        dwell = (now - entered).total_seconds()
        if dwell >= 1.0:   # passing through a corner of the zone is not queueing
            self._exits.append((now, dwell))

    def track_expired(self, key: tuple[str, int], now: datetime) -> None:
        if self.zone.kind == "queue":
            self._record_exit(key, now)

    def reset(self) -> None:
        self._samples.clear()
        self._entered.clear()
        self.state = None


# ---------------------------------------------------------------------------
# Minute buckets for history / trend charts
# ---------------------------------------------------------------------------
@dataclass
class _Bucket:
    start: datetime
    samples: int = 0
    total: int = 0
    maximum: int = 0
    entries: int = 0
    exits: int = 0


def _minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


# ---------------------------------------------------------------------------
# The processor
# ---------------------------------------------------------------------------
class CrowdAnalyticsProcessor:
    """FrameProcessor producing line-crossing events, alerts and count history."""

    def __init__(self, camera_db_id: int, camera_id: str, config: AnalyticsConfig,
                 *, persist_snapshots: bool = True) -> None:
        self.camera_db_id = camera_db_id
        self.camera_id = camera_id
        self.config = config
        self._persist_snapshots = persist_snapshots
        self._lines = [_LineCounter(line) for line in config.lines]
        self._zones = [_ZoneMonitor(z, config.alerts.smoothing_seconds) for z in config.zones]
        self._buckets: dict[str, _Bucket] = {}
        self._snapshot: AnalyticsSnapshot | None = None

    # -------------------------------------------------------------- reading
    def snapshot(self) -> AnalyticsSnapshot | None:
        return self._snapshot

    # ------------------------------------------------------------ processing
    def process(self, result: PipelineResult) -> list[Any]:
        tracking = result.tracking
        if tracking is None:
            return []
        now = result.timestamp
        records: list[Any] = []

        if tracking.session_ended:
            return self._end_session(now)

        expired_keys = [(t.tracker_session, t.track_id) for t in tracking.expired]
        for key in expired_keys:
            for counter in self._lines:
                counter.forget(key)
            for monitor in self._zones:
                monitor.track_expired(key, now)

        if result.frame_shape is None:
            return records
        height, width = result.frame_shape[0], result.frame_shape[1]
        confirmed = [t for t in tracking.tracks if t.hits >= self.config.min_track_hits]

        entries = exits = 0
        for counter in self._lines:
            for track, direction in counter.update(
                confirmed, width, height, self.config.recount_cooldown_seconds
            ):
                if direction == "IN":
                    entries += 1
                else:
                    exits += 1
                records.append(TrackEventRecord(
                    camera_db_id=self.camera_db_id,
                    event_type=VisitorEventType.ENTERED if direction == "IN" else VisitorEventType.EXITED,
                    tracking_id=track.track_id,
                    event_time=now,
                    metadata={
                        "source": "line_crossing",
                        "anonymous": True,
                        "line_id": counter.line.id,
                        "direction": direction,
                        "tracker_session": track.tracker_session,
                    },
                ))

        zone_states = [m.update(confirmed, width, height, now) for m in self._zones]
        for monitor in self._zones:
            records.extend(self._evaluate_alerts(monitor, now))

        records.extend(self._accumulate(now, len(confirmed), zone_states, entries, exits))

        self._snapshot = AnalyticsSnapshot(
            confirmed_count=len(confirmed),
            entries=sum(c.entries for c in self._lines),
            exits=sum(c.exits for c in self._lines),
            lines=tuple(LineState(c.line.id, c.line.name, c.entries, c.exits) for c in self._lines),
            zones=tuple(zone_states),
            alerts=tuple(a for m in self._zones for a in (m.density_alert.active, m.queue_alert.active) if a),
            updated_at=now,
        )
        return records

    # ---------------------------------------------------------------- alerts
    def _evaluate_alerts(self, monitor: _ZoneMonitor, now: datetime) -> list[AlertRecord]:
        state = monitor.state
        if state is None:
            return []
        settings = self.config.alerts
        out: list[AlertRecord] = []

        t = monitor.zone.thresholds
        level_threshold = {DensityLevel.MEDIUM: t.medium, DensityLevel.HIGH: t.high,
                           DensityLevel.CRITICAL: t.critical}.get(settings.min_level, t.high)
        out.extend(self._debounce(
            monitor, monitor.density_alert, now,
            condition=state.level.rank >= settings.min_level.rank,
            alert_type="CROWD_DENSITY", value=state.density, threshold=level_threshold,
            severity="critical" if state.level is DensityLevel.CRITICAL else "warning",
            message=f"{monitor.zone.name or monitor.zone.id}: crowd density {state.level.value} "
                    f"({state.density:g} {state.density_unit.replace('_', ' ')})",
        ))
        if monitor.zone.kind == "queue" and monitor.zone.queue_alert_length:
            limit = monitor.zone.queue_alert_length
            out.extend(self._debounce(
                monitor, monitor.queue_alert, now,
                condition=state.smoothed_count >= limit,
                alert_type="QUEUE_CONGESTION", value=state.smoothed_count, threshold=float(limit),
                severity="critical" if state.smoothed_count >= 1.5 * limit else "warning",
                message=f"{monitor.zone.name or monitor.zone.id}: queue length "
                        f"{state.smoothed_count:g} (limit {limit})",
            ))
        return out

    def _debounce(self, monitor: _ZoneMonitor, alert: _AlertState, now: datetime, *,
                  condition: bool, alert_type: str, value: float, threshold: float,
                  severity: str, message: str) -> list[AlertRecord]:
        settings = self.config.alerts
        if condition:
            alert.false_since = None
            alert.true_since = alert.true_since or now
            if alert.active is None:
                if (now - alert.true_since).total_seconds() >= settings.raise_after_seconds:
                    alert.peak = value
                    alert.active = ActiveAlert(
                        alert_id=uuid.uuid4().hex, alert_type=alert_type,
                        zone_id=monitor.zone.id, severity=severity, started_at=now,
                        value=value, threshold=threshold, message=message,
                    )
                    return [self._alert_record(alert.active, "RAISE", value)]
                return []
            # Already active: track the peak and escalate severity once.
            alert.peak = max(alert.peak, value)
            if severity == "critical" and alert.active.severity != "critical":
                alert.active = replace(alert.active, severity="critical", value=value,
                                       message=message)
                return [self._alert_record(alert.active, "ESCALATE", alert.peak)]
            alert.active = replace(alert.active, value=value)
            return []

        alert.true_since = None
        if alert.active is None:
            return []
        alert.false_since = alert.false_since or now
        if (now - alert.false_since).total_seconds() >= settings.clear_after_seconds:
            record = self._alert_record(alert.active, "CLEAR", alert.peak, ended_at=now)
            alert.active, alert.false_since = None, None
            return [record]
        return []

    def _alert_record(self, alert: ActiveAlert, action: str, peak: float,
                      ended_at: datetime | None = None, reason: str | None = None) -> AlertRecord:
        return AlertRecord(
            alert_id=alert.alert_id, action=action, camera_db_id=self.camera_db_id,
            alert_type=alert.alert_type, zone_id=alert.zone_id, severity=alert.severity,
            started_at=alert.started_at, ended_at=ended_at, peak_value=round(peak, 3),
            threshold=alert.threshold, message=alert.message,
            metadata={"reason": reason} if reason else None,
        )

    # ----------------------------------------------------------- history
    def _accumulate(self, now: datetime, total_count: int, zones: list[ZoneState],
                    entries: int, exits: int) -> list[CountSnapshotRecord]:
        if not self._persist_snapshots:
            return []
        minute = _minute(now)
        out: list[CountSnapshotRecord] = []
        values = {WHOLE_FRAME: total_count, **{z.zone_id: z.count for z in zones}}
        for zone_id, value in values.items():
            bucket = self._buckets.get(zone_id)
            if bucket is not None and bucket.start != minute:
                out.append(self._bucket_record(zone_id, bucket))
                bucket = None
            if bucket is None:
                bucket = self._buckets[zone_id] = _Bucket(minute)
            bucket.samples += 1
            bucket.total += value
            bucket.maximum = max(bucket.maximum, value)
            if zone_id == WHOLE_FRAME:
                bucket.entries += entries
                bucket.exits += exits
        return out

    def _bucket_record(self, zone_id: str, bucket: _Bucket) -> CountSnapshotRecord:
        return CountSnapshotRecord(
            camera_db_id=self.camera_db_id, zone_id=zone_id, bucket_start=bucket.start,
            samples=bucket.samples, avg_count=bucket.total / bucket.samples,
            max_count=bucket.maximum, entries=bucket.entries, exits=bucket.exits,
        )

    def _end_session(self, now: datetime) -> list[Any]:
        """Stream lost or worker stopping: flush history, close open alerts."""
        records: list[Any] = []
        if self._persist_snapshots:
            records.extend(self._bucket_record(z, b) for z, b in self._buckets.items() if b.samples)
        self._buckets.clear()
        for monitor in self._zones:
            for alert in (monitor.density_alert, monitor.queue_alert):
                if alert.active is not None:
                    records.append(self._alert_record(
                        alert.active, "CLEAR", alert.peak, ended_at=now, reason="no_video"))
                alert.active = alert.true_since = alert.false_since = None
            monitor.reset()
        for counter in self._lines:
            counter.reset()
        if self._snapshot is not None:
            # Totals survive a reconnect; live zone values do not (unknown, not zero).
            self._snapshot = AnalyticsSnapshot(
                confirmed_count=0, entries=self._snapshot.entries, exits=self._snapshot.exits,
                lines=self._snapshot.lines, zones=(), alerts=(), updated_at=now,
            )
        return records
