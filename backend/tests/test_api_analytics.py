"""Crowd analytics API: live overview, config, history, alerts, preview."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import select

from app.ai.analytics import AnalyticsSnapshot, LineState, ZoneState
from app.ai.analytics_config import DensityLevel
from app.camera.state import Connection, manager_status, runtime_registry
from app.config import get_settings
from app.models import Camera, CrowdAlert, CrowdCountSnapshot, VisitorEvent
from app.services.analytics_service import site_day_bounds
from app.utils.time import utc_now

pytestmark = pytest.mark.db

CONFIG = {
    "tuning": {"image_size": 960, "process_fps": 3},
    "lines": [{"id": "GATE", "name": "Main gate", "start": [0.1, 0.6], "end": [0.9, 0.6]}],
    "zones": [{"id": "QUEUE", "name": "Food queue", "kind": "queue",
               "polygon": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]], "queue_alert_length": 15}],
}


@pytest.fixture(autouse=True)
def clean_runtime():
    runtime_registry.clear()
    yield
    runtime_registry.clear()
    manager_status.set(manager_status.get().state.__class__("disabled"))


def camera_row(session, code):
    return session.scalar(select(Camera).where(Camera.camera_id == code))


# ------------------------------------------------------------------ config
def test_config_roundtrip_and_validation(api_client, api_camera):
    r = api_client.put("/api/cameras/ANN-ENT-01/analytics", json=CONFIG)
    assert r.status_code == 200, r.text
    got = api_client.get("/api/cameras/ANN-ENT-01/analytics").json()
    assert got["lines"][0]["id"] == "GATE" and got["tuning"]["image_size"] == 960
    assert api_client.get("/api/cameras/ANN-ENT-01").json()["has_analytics"] is True

    bad = {**CONFIG, "lines": [{"id": "L", "start": [0, 0], "end": [2, 2]}]}
    r = api_client.put("/api/cameras/ANN-ENT-01/analytics", json=bad)
    assert r.status_code == 422 and r.json()["code"] == "invalid_request"
    assert api_client.put("/api/cameras/NOPE/analytics", json=CONFIG).status_code == 404


def test_clearing_config_stores_null(api_client, api_camera, session):
    api_client.put("/api/cameras/ANN-ENT-01/analytics", json=CONFIG)
    api_client.put("/api/cameras/ANN-ENT-01/analytics", json={})
    assert camera_row(session, "ANN-ENT-01").analytics_config is None


# -------------------------------------------------------------------- live
def test_live_overview_idle_reports_unknown_not_zero(api_client, api_camera):
    body = api_client.get("/api/analytics/live").json()
    assert body["mode"] == "idle" and body["people_now"] is None
    cam = body["cameras"][0]
    assert cam["person_count"] is None and cam["confirmed_count"] is None
    assert cam["source_kind"] == "live" and cam["ai_running"] is False


def test_live_overview_with_running_camera(api_client, api_camera, session):
    api_client.put("/api/cameras/ANN-ENT-01/analytics", json=CONFIG)
    now = utc_now()
    runtime = runtime_registry.register("ANN-ENT-01")
    runtime.update(
        ai_running=True, connected=True, connection_state=Connection.ONLINE,
        person_count=7, processing_fps=4.9, inference_ms=81.2, last_frame_timestamp=now,
        analytics=AnalyticsSnapshot(
            confirmed_count=6, entries=3, exits=1,
            lines=(LineState("GATE", "Main gate", 3, 1),),
            zones=(ZoneState("QUEUE", "Food queue", "queue", 4, 4.0, 4.0, "people",
                             DensityLevel.LOW, queue_length=4, estimated_wait_seconds=120.0,
                             throughput_per_minute=2.0),),
            alerts=(), updated_at=now),
    )
    cam_id = camera_row(session, "ANN-ENT-01").id
    session.add_all([
        VisitorEvent(camera_id=cam_id, event_type="ENTERED", tracking_id=1, event_time=now,
                     event_metadata={"source": "line_crossing"}),
        VisitorEvent(camera_id=cam_id, event_type="ENTERED", tracking_id=2, event_time=now,
                     event_metadata={"source": "line_crossing"}),
        VisitorEvent(camera_id=cam_id, event_type="EXITED", tracking_id=3, event_time=now,
                     event_metadata={"source": "line_crossing"}),
        # not a line crossing: must not be counted
        VisitorEvent(camera_id=cam_id, event_type="ENTERED", tracking_id=4, event_time=now,
                     event_metadata={"source": "manual"}),
    ])
    session.flush()

    body = api_client.get("/api/analytics/live").json()
    assert body["mode"] == "live" and body["people_now"] == 6
    assert (body["entries_today"], body["exits_today"]) == (2, 1)
    cam = body["cameras"][0]
    assert cam["confirmed_count"] == 6 and cam["inference_ms"] == 81.2
    assert cam["zones"][0]["estimated_wait_seconds"] == 120.0
    assert cam["lines"][0]["entries"] == 3
    assert cam["stale"] is False
    assert any("5 processed frames" in w for w in cam["warnings"])   # lines at 3 fps


def test_demo_cameras_make_the_mode_demo(api_client):
    api_client.post("/api/cameras", json={"camera_id": "DEMO-Q", "camera_name": "Queue (recorded)",
                                          "rtsp_url": "demo://queue.mp4"})
    runtime_registry.register("DEMO-Q").update(ai_running=True, connected=True,
                                               connection_state=Connection.ONLINE,
                                               source_kind="recorded")
    body = api_client.get("/api/analytics/live").json()
    assert body["mode"] == "demo"
    assert body["cameras"][0]["source_kind"] == "recorded"


def test_stale_stream_is_flagged(api_client, api_camera):
    runtime_registry.register("ANN-ENT-01").update(
        ai_running=True, connected=True, connection_state=Connection.ONLINE, person_count=2,
        last_frame_timestamp=utc_now() - timedelta(minutes=5))
    assert api_client.get("/api/analytics/live").json()["cameras"][0]["stale"] is True


def test_site_day_bounds_follow_the_temple_time_zone():
    # 20:00 UTC on 1 Oct is 01:30 on 2 Oct in India
    start, end = site_day_bounds("Asia/Kolkata", datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc))
    assert start == datetime(2026, 10, 1, 18, 30, tzinfo=timezone.utc)
    assert end - start == timedelta(days=1)


# ----------------------------------------------------------------- history
def test_history_buckets_and_camera_sum(api_client, api_camera, session):
    api_client.post("/api/cameras", json={"camera_id": "HALL-1", "camera_name": "Hall"})
    a, b = camera_row(session, "ANN-ENT-01").id, camera_row(session, "HALL-1").id
    base = utc_now().replace(second=0, microsecond=0) - timedelta(minutes=30)
    base = base - timedelta(minutes=base.minute % 5)
    for m in range(5):
        session.add(CrowdCountSnapshot(camera_id=a, zone_id="*", bucket_start=base + timedelta(minutes=m),
                                       samples=10, avg_count=2.0 + m, max_count=4 + m, entries=1, exits=0))
        session.add(CrowdCountSnapshot(camera_id=b, zone_id="*", bucket_start=base + timedelta(minutes=m),
                                       samples=10, avg_count=10.0, max_count=12, entries=0, exits=1))
    session.flush()

    one = api_client.get("/api/analytics/history", params={"camera_id": "ANN-ENT-01",
                                                           "minutes": 60, "bucket_minutes": 5}).json()
    assert len(one["points"]) == 1
    p = one["points"][0]
    assert p["avg_count"] == 4.0 and p["max_count"] == 8 and p["entries"] == 5

    total = api_client.get("/api/analytics/history", params={"minutes": 60, "bucket_minutes": 5}).json()
    p = total["points"][0]
    assert p["avg_count"] == 14.0 and p["entries"] == 5 and p["exits"] == 5

    assert api_client.get("/api/analytics/history", params={"bucket_minutes": 7}).status_code == 422
    assert api_client.get("/api/analytics/history", params={"zone_id": "x' OR 1=1"}).status_code == 422


# ------------------------------------------------------------------ alerts
def test_alert_list_filter_and_acknowledge(api_client, api_camera, session):
    cid = camera_row(session, "ANN-ENT-01").id
    session.add_all([
        CrowdAlert(alert_uid="a" * 32, camera_id=cid, zone_id="QUEUE", alert_type="QUEUE_CONGESTION",
                   severity="warning", started_at=utc_now(), threshold=15, message="queue long"),
        CrowdAlert(alert_uid="b" * 32, camera_id=cid, zone_id="HALL", alert_type="CROWD_DENSITY",
                   severity="critical", started_at=utc_now() - timedelta(hours=1),
                   ended_at=utc_now(), threshold=30),
    ])
    session.flush()
    active = api_client.get("/api/alerts", params={"active": True}).json()
    assert [a["alert_id"] for a in active["items"]] == ["a" * 32]
    assert api_client.get("/api/alerts").json()["total"] == 2
    assert api_client.get("/api/analytics/live").json()["open_alerts"] == 1

    r = api_client.post(f"/api/alerts/{'a' * 32}/acknowledge")
    assert r.status_code == 200 and r.json()["acknowledged_at"] is not None
    assert api_client.post(f"/api/alerts/{'c' * 32}/acknowledge").status_code == 404
    assert api_client.post("/api/alerts/not-hex/acknowledge").status_code == 422


# ----------------------------------------------------------------- preview
def test_preview_disabled_by_default(api_client, api_camera):
    assert api_client.get("/api/cameras/ANN-ENT-01/preview.jpg").status_code == 404


def test_preview_renders_anonymised_jpeg(api_client, api_camera):
    from app.ai.types import TrackedObject, TrackState

    settings = get_settings()
    object.__setattr__(settings, "preview_enabled", True)
    try:
        assert api_client.get("/api/cameras/ANN-ENT-01/preview.jpg").status_code == 404  # no frame yet
        api_client.put("/api/cameras/ANN-ENT-01/analytics", json=CONFIG)
        frame = np.full((360, 640, 3), 200, np.uint8)
        # fine detail (a 2-px checkerboard) where a face would be: pixelation must destroy it
        checker = (np.indices((40, 40)).sum(axis=0) // 2 % 2 * 255).astype(np.uint8)
        frame[40:80, 100:140] = checker[..., None]
        frame.flags.writeable = False
        now = utc_now()
        track = TrackedObject("ANN-ENT-01", "s", 1, 0, "person", 0.9, 100, 40, 140, 200, now,
                              TrackState.TRACKED, 5, now, now)
        runtime_registry.register("ANN-ENT-01").set_preview(frame, (track,), now)
        r = api_client.get("/api/cameras/ANN-ENT-01/preview.jpg")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        import cv2

        img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        assert img.shape[1] == 640
        assert img[45:75, 105:135].std() < 40      # detail gone (was ~127)
    finally:
        object.__setattr__(settings, "preview_enabled", False)
