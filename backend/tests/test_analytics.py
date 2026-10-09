"""Crowd analytics: line crossing, zones, queues, alerts and count history.

Deterministic: tracks are constructed directly, so these tests check the
counting logic itself, independent of any detector.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.ai.analytics import (
    WHOLE_FRAME,
    CrowdAnalyticsProcessor,
    density_level,
    point_in_polygon,
    segments_intersect,
    signed_distance,
)
from app.ai.analytics_config import AnalyticsConfig, DensityLevel, parse_analytics_config
from app.ai.pipeline_types import PipelineResult
from app.ai.types import TrackedObject, TrackingResult, TrackState
from app.models.enums import VisitorEventType
from app.services.runtime_events import AlertRecord, CountSnapshotRecord, TrackEventRecord
from tests.helpers import ts

W, H = 1000, 500
SESSION = "sess1"


def track(tid: int, foot_x: float, foot_y: float, when: float, hits: int = 5,
          session: str = SESSION) -> TrackedObject:
    return TrackedObject(
        camera_id="CAM", tracker_session=session, track_id=tid, class_id=0, class_name="person",
        confidence=0.9, x1=foot_x - 20, y1=foot_y - 100, x2=foot_x + 20, y2=foot_y,
        timestamp=ts(when), state=TrackState.TRACKED, hits=hits,
        first_seen=ts(0), last_seen=ts(when),
    )


def frame_result(tracks, when: float, expired=(), session_ended=False) -> PipelineResult:
    tracking = TrackingResult(
        camera_id="CAM", tracker_session=SESSION, timestamp=ts(when), tracks=tuple(tracks),
        lost=(), expired=tuple(expired), events=(), session_ended=session_ended,
    )
    return PipelineResult(
        camera_id="CAM", frame_sequence=1, timestamp=ts(when), usable_frame=True,
        detections=(), confident_detections=len(tracks), tracking=tracking,
        frame_shape=(H, W),
    )


# A vertical line at x = 0.5: side A is left (x < 500), side B is right.
VERTICAL_LINE = {"id": "GATE", "name": "Gate", "start": [0.5, 0.0], "end": [0.5, 1.0]}


def processor(**config) -> CrowdAnalyticsProcessor:
    return CrowdAnalyticsProcessor(1, "CAM", AnalyticsConfig.model_validate(config))


def crossings(records):
    return [(r.event_type, r.metadata["line_id"]) for r in records
            if isinstance(r, TrackEventRecord)]


def walk(proc, tid, xs, y=250.0, start=0.0, step=0.2, hits=5):
    out = []
    for i, x in enumerate(xs):
        out += proc.process(frame_result([track(tid, x, y, start + i * step, hits)], start + i * step))
    return out


# ------------------------------------------------------------------ geometry
def test_signed_distance_sides():
    # Downward vertical line: its left-hand side (screen right, x>500) is side A.
    s, e = (500, 0), (500, 500)
    assert signed_distance((400, 250), s, e) > 0
    assert signed_distance((600, 250), s, e) < 0
    assert signed_distance((500, 100), s, e) == 0


def test_segments_intersect_and_slack():
    assert segments_intersect((0, 0), (10, 10), (0, 10), (10, 0))
    assert not segments_intersect((0, 0), (1, 1), (5, 5), (6, 0))
    # passes just beyond the end of a segment: only counts with slack
    assert not segments_intersect((11, -1), (11, 1), (0, 0), (10, 0))
    assert segments_intersect((11, -1), (11, 1), (0, 0), (10, 0), slack=2)


def test_point_in_polygon():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert point_in_polygon((5, 5), square)
    assert not point_in_polygon((15, 5), square)


def test_density_level_boundaries():
    assert density_level(9.9, 10, 20, 30) is DensityLevel.LOW
    assert density_level(10, 10, 20, 30) is DensityLevel.MEDIUM
    assert density_level(20, 10, 20, 30) is DensityLevel.HIGH
    assert density_level(30, 10, 20, 30) is DensityLevel.CRITICAL


# ------------------------------------------------------------------- config
def test_config_rejects_bad_geometry_and_duplicates():
    with pytest.raises(ValidationError):
        AnalyticsConfig.model_validate({"lines": [{"id": "L", "start": [0.5, 0.5], "end": [0.5, 0.5]}]})
    with pytest.raises(ValidationError):
        AnalyticsConfig.model_validate({"zones": [{"id": "Z", "polygon": [[0, 0], [1, 1], [0.5, 0.5]]}]})
    with pytest.raises(ValidationError):
        AnalyticsConfig.model_validate({"lines": [VERTICAL_LINE, VERTICAL_LINE]})
    with pytest.raises(ValidationError):   # coordinates are normalised
        AnalyticsConfig.model_validate({"lines": [{"id": "L", "start": [0, 0], "end": [640, 480]}]})
    with pytest.raises(ValidationError):   # thresholds must be ordered
        AnalyticsConfig.model_validate({"zones": [{"id": "Z", "polygon": [[0, 0], [1, 0], [1, 1]],
                                                   "thresholds": {"medium": 5, "high": 4, "critical": 9}}]})
    with pytest.raises(ValidationError):   # per m2 needs an area
        AnalyticsConfig.model_validate({"zones": [{"id": "Z", "polygon": [[0, 0], [1, 0], [1, 1]],
                                                   "thresholds": {"per_square_metre": True}}]})
    with pytest.raises(ValidationError):   # unknown keys are typos, not silently ignored
        AnalyticsConfig.model_validate({"linez": []})
    assert parse_analytics_config(None).is_empty


# ------------------------------------------------------------- line counting
def test_side_a_to_side_b_is_an_entry_and_back_is_an_exit():
    proc = processor(lines=[VERTICAL_LINE])
    # side A must be where signed distance < 0: x > 500 for this downward line
    rec = walk(proc, 1, [700, 600, 520, 480, 400, 300])
    assert crossings(rec) == [(VisitorEventType.ENTERED, "GATE")]
    rec = walk(proc, 1, [300, 400, 600, 700], start=10)
    assert crossings(rec) == [(VisitorEventType.EXITED, "GATE")]
    snap = proc.snapshot()
    assert (snap.entries, snap.exits) == (1, 1)
    assert snap.lines[0].entries == 1


def test_invert_swaps_directions():
    proc = processor(lines=[{**VERTICAL_LINE, "invert": True}])
    rec = walk(proc, 1, [700, 600, 400, 300])
    assert crossings(rec) == [(VisitorEventType.EXITED, "GATE")]


def test_jitter_on_the_line_is_not_counted():
    proc = processor(lines=[VERTICAL_LINE])   # band = 0.02 * diag ~ 22 px
    rec = walk(proc, 1, [700, 505, 495, 507, 493, 510, 490, 700])
    assert crossings(rec) == []


def test_unconfirmed_tracks_are_never_counted():
    proc = processor(lines=[VERTICAL_LINE], min_track_hits=3)
    rec = walk(proc, 1, [700, 600, 400, 300], hits=2)
    assert crossings(rec) == []


def test_walking_around_the_end_of_a_short_line_is_not_a_crossing():
    short = {"id": "DOOR", "start": [0.5, 0.0], "end": [0.5, 0.3]}  # y 0..150 px
    proc = processor(lines=[short])
    rec = walk(proc, 1, [700, 600, 400, 300], y=400)   # passes below the line's end
    assert crossings(rec) == []


def test_recount_cooldown_blocks_flicker_but_not_a_real_return():
    proc = processor(lines=[VERTICAL_LINE], recount_cooldown_seconds=5)
    rec = walk(proc, 1, [700, 300, 700, 300], step=0.5)   # in, out, in within 1.5 s
    assert [e for e, _ in crossings(rec)] == [VisitorEventType.ENTERED, VisitorEventType.EXITED]
    rec = walk(proc, 1, [700, 300], start=20)            # 20 s later: genuine out and in
    assert [e for e, _ in crossings(rec)] == [VisitorEventType.EXITED, VisitorEventType.ENTERED]


def test_two_people_crossing_in_opposite_directions():
    proc = processor(lines=[VERTICAL_LINE])
    out = []
    for i, (a, b) in enumerate(zip([700, 600, 400, 300], [300, 400, 600, 700])):
        out += proc.process(frame_result([track(1, a, 200, i), track(2, b, 300, i)], i))
    assert sorted(e for e, _ in crossings(out)) == sorted(
        [VisitorEventType.ENTERED, VisitorEventType.EXITED])


def test_crossing_events_are_anonymous():
    proc = processor(lines=[VERTICAL_LINE])
    rec = [r for r in walk(proc, 7, [700, 300]) if isinstance(r, TrackEventRecord)]
    assert rec[0].metadata["anonymous"] is True
    assert rec[0].tracking_id == 7
    assert "visitor" not in rec[0].metadata


# -------------------------------------------------------------------- zones
SQUARE = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]   # left half


def test_zone_counts_people_by_their_feet():
    proc = processor(zones=[{"id": "HALL", "polygon": SQUARE,
                             "thresholds": {"medium": 2, "high": 3, "critical": 4}}],
                     alerts={"smoothing_seconds": 0})
    proc.process(frame_result([track(1, 100, 250, 0), track(2, 200, 250, 0), track(3, 800, 250, 0)], 0))
    zone = proc.snapshot().zones[0]
    assert zone.count == 2 and zone.level is DensityLevel.MEDIUM
    assert proc.snapshot().confirmed_count == 3


def test_zone_smoothing_ignores_a_one_frame_spike():
    proc = processor(zones=[{"id": "Z", "polygon": SQUARE}], alerts={"smoothing_seconds": 3})
    for i in range(10):
        n = 12 if i == 5 else 2
        proc.process(frame_result([track(k, 100 + k, 250, i * 0.2) for k in range(n)], i * 0.2))
        if i == 5:
            assert proc.snapshot().zones[0].count == 12
            assert proc.snapshot().zones[0].smoothed_count == 2


def test_density_per_square_metre():
    proc = processor(zones=[{"id": "Z", "polygon": SQUARE, "area_m2": 4.0,
                             "thresholds": {"medium": 1, "high": 2, "critical": 4,
                                            "per_square_metre": True}}],
                     alerts={"smoothing_seconds": 0})
    proc.process(frame_result([track(k, 100 + k, 250, 0) for k in range(8)], 0))
    z = proc.snapshot().zones[0]
    assert z.density == 2.0 and z.density_unit == "people_per_m2" and z.level is DensityLevel.HIGH


# -------------------------------------------------------------------- queue
def test_queue_wait_and_throughput():
    proc = processor(zones=[{"id": "Q", "kind": "queue", "polygon": SQUARE}],
                     alerts={"smoothing_seconds": 0})
    # person 1 queues 0..30 s then leaves; person 2 queues 10..50 s then leaves
    for t in range(0, 61, 1):
        people = []
        if t < 30:
            people.append(track(1, 100, 250, t))
        else:
            people.append(track(1, 900, 250, t))
        if 10 <= t < 50:
            people.append(track(2, 150, 250, t))
        proc.process(frame_result(people, t))
    q = proc.snapshot().zones[0]
    assert q.queue_length == 0
    assert q.avg_wait_seconds == pytest.approx(35.0)       # (30 + 40) / 2
    assert q.throughput_per_minute == pytest.approx(0.4)   # 2 exits in a 5-min window


def test_queue_estimated_wait_uses_littles_law():
    proc = processor(zones=[{"id": "Q", "kind": "queue", "polygon": SQUARE}],
                     alerts={"smoothing_seconds": 0})
    t = 0.0
    for k in range(5):               # 5 people served, each after 10 s
        proc.process(frame_result([track(k, 100, 250, t)], t))
        t += 10
        proc.process(frame_result([track(k, 900, 250, t)], t))
    proc.process(frame_result([track(10 + k, 100 + k, 250, t) for k in range(4)], t))
    q = proc.snapshot().zones[0]
    assert q.throughput_per_minute == 1.0              # 5 exits / 5 min
    assert q.estimated_wait_seconds == pytest.approx(240.0)   # 4 waiting / 1 per min


def test_expired_track_inside_queue_counts_as_left():
    proc = processor(zones=[{"id": "Q", "kind": "queue", "polygon": SQUARE}],
                     alerts={"smoothing_seconds": 0})
    proc.process(frame_result([track(1, 100, 250, 0)], 0))
    proc.process(frame_result([], 20, expired=[track(1, 100, 250, 20)]))
    assert proc.snapshot().zones[0].avg_wait_seconds == pytest.approx(20.0)


# ------------------------------------------------------------------- alerts
def crowd(n, when):
    return frame_result([track(k, 50 + k * 5, 250, when) for k in range(n)], when)


def alert_cfg(**alerts):
    return dict(zones=[{"id": "HALL", "polygon": SQUARE,
                        "thresholds": {"medium": 3, "high": 5, "critical": 8}}],
                alerts={"smoothing_seconds": 0, **alerts})


def alerts_in(records):
    return [r for r in records if isinstance(r, AlertRecord)]


def test_alert_raised_once_after_sustained_density_then_cleared():
    proc = processor(**alert_cfg(raise_after_seconds=5, clear_after_seconds=10))
    out = []
    for t in range(0, 20):                      # crowded for 20 s
        out += proc.process(crowd(6, t))
    raised = alerts_in(out)
    assert [a.action for a in raised] == ["RAISE"]
    assert raised[0].started_at == ts(5)
    assert raised[0].severity == "warning" and raised[0].alert_type == "CROWD_DENSITY"
    assert len(proc.snapshot().alerts) == 1

    out = []
    for t in range(20, 40):                     # calm
        out += proc.process(crowd(1, t))
    cleared = alerts_in(out)
    assert [a.action for a in cleared] == ["CLEAR"]
    assert cleared[0].ended_at == ts(30) and cleared[0].peak_value == 6
    assert proc.snapshot().alerts == ()


def test_short_spike_raises_no_alert():
    proc = processor(**alert_cfg(raise_after_seconds=5))
    out = []
    for t in range(0, 30):
        out += proc.process(crowd(9 if 10 <= t < 13 else 1, t))
    assert alerts_in(out) == []


def test_brief_dip_does_not_clear_an_active_alert():
    proc = processor(**alert_cfg(raise_after_seconds=0, clear_after_seconds=10))
    out = []
    for t in range(0, 30):
        out += proc.process(crowd(1 if t in (10, 11, 12) else 6, t))
    assert [a.action for a in alerts_in(out)] == ["RAISE"]


def test_alert_escalates_to_critical_once():
    proc = processor(**alert_cfg(raise_after_seconds=0))
    out = []
    for t, n in enumerate([6, 6, 9, 9, 10]):
        out += proc.process(crowd(n, t))
    actions = [(a.action, a.severity) for a in alerts_in(out)]
    assert actions == [("RAISE", "warning"), ("ESCALATE", "critical")]


def test_queue_congestion_alert():
    proc = processor(zones=[{"id": "Q", "kind": "queue", "polygon": SQUARE, "queue_alert_length": 4,
                             "thresholds": {"medium": 50, "high": 60, "critical": 70}}],
                     alerts={"smoothing_seconds": 0, "raise_after_seconds": 2})
    out = []
    for t in range(5):
        out += proc.process(crowd(5, t))
    alerts = alerts_in(out)
    assert [(a.alert_type, a.action) for a in alerts] == [("QUEUE_CONGESTION", "RAISE")]
    assert alerts[0].threshold == 4


def test_session_end_closes_alerts_and_flushes_history_but_keeps_totals():
    proc = processor(lines=[VERTICAL_LINE], **alert_cfg(raise_after_seconds=0))
    walk(proc, 99, [700, 300])
    proc.process(crowd(6, 1))
    out = proc.process(frame_result([], 2, session_ended=True))
    assert [a.action for a in alerts_in(out)] == ["CLEAR"]
    assert alerts_in(out)[0].metadata == {"reason": "no_video"}
    assert any(isinstance(r, CountSnapshotRecord) for r in out)
    snap = proc.snapshot()
    assert snap.entries == 1 and snap.zones == () and snap.alerts == ()


# ------------------------------------------------------------------ history
def test_per_minute_buckets():
    proc = processor(zones=[{"id": "Z", "polygon": SQUARE}], lines=[VERTICAL_LINE],
                     alerts={"smoothing_seconds": 0})
    out = []
    for t in range(0, 60, 10):                         # minute 12:00: counts 2
        out += proc.process(frame_result([track(1, 100, 250, t), track(2, 120, 250, t)], t))
    out += walk(proc, 5, [700, 300], start=55, step=1)  # one entry at 12:00:56
    out += proc.process(frame_result([track(1, 100, 250, 61)], 61))   # minute rolls over
    buckets = {r.zone_id: r for r in out if isinstance(r, CountSnapshotRecord)}
    assert set(buckets) == {WHOLE_FRAME, "Z"}
    whole = buckets[WHOLE_FRAME]
    assert whole.bucket_start == ts(0) and whole.max_count == 2 and whole.entries == 1
    assert buckets["Z"].max_count == 2


def test_history_can_be_disabled():
    proc = CrowdAnalyticsProcessor(1, "CAM", AnalyticsConfig(), persist_snapshots=False)
    out = []
    for t in range(0, 130, 10):
        out += proc.process(crowd(2, t))
    assert out == []
    assert proc.snapshot().confirmed_count == 2


def test_frame_without_shape_only_ages_state():
    proc = processor(lines=[VERTICAL_LINE])
    result = frame_result([track(1, 700, 250, 0)], 0)
    result = PipelineResult(**{**result.__dict__, "frame_shape": None})
    assert proc.process(result) == []


def test_timestamps_are_monotonic_utc():
    proc = processor(**alert_cfg(raise_after_seconds=0))
    rec = alerts_in(proc.process(crowd(6, 0)))[0]
    assert rec.started_at.tzinfo is not None
    assert rec.started_at + timedelta(0) == ts(0)
