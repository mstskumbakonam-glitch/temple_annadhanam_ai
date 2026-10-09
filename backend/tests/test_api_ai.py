"""AI runtime status endpoints. They report real runtime state - nothing invented."""

import pytest

from app.camera.state import (
    Connection,
    ManagerState,
    manager_status,
    runtime_registry,
)
from tests.helpers import ts

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def clean_runtime_state():
    """The runtime registry and manager status are process-wide: isolate every test."""
    runtime_registry.clear()
    manager_status.set(ManagerState.DISABLED, model_loaded=False, model_name=None, device=None)
    yield
    runtime_registry.clear()
    manager_status.set(ManagerState.DISABLED, model_loaded=False, model_name=None, device=None)


def running(camera_id, **fields):
    runtime = runtime_registry.register(camera_id)
    runtime.update(
        connected=True, ai_running=True, model_loaded=True,
        connection_state=Connection.ONLINE, person_count=7, active_tracks=8,
        processing_fps=4.8, capture_fps=24.9, frames_processed=120, frames_captured=600,
        last_frame_timestamp=ts(10), last_detection_timestamp=ts(9),
        tracker_session="abc123def456", **fields,
    )
    return runtime


# ----------------------------------------------------- when AI is not running
def test_status_says_clearly_that_ai_is_not_running(api_client, api_camera):
    body = api_client.get("/api/ai/status").json()
    assert body["state"] == "disabled"
    assert body["autostart_enabled"] is False
    assert body["model_loaded"] is False
    assert body["cameras_total"] == 1 and body["cameras_running"] == 0
    camera = body["cameras"][0]
    assert camera["camera_id"] == api_camera["camera_id"]
    assert camera["ai_running"] is False and camera["connected"] is False
    assert camera["connection_state"] == "not_running"


def test_counts_are_null_not_zero_when_nothing_is_running(api_client, api_camera):
    """Zero would claim 'nobody is there'; the honest answer is 'unknown'."""
    camera = api_client.get(f"/api/cameras/{api_camera['camera_id']}/ai-status").json()
    assert camera["person_count"] is None
    assert camera["active_tracks"] is None
    assert camera["processing_fps"] is None
    assert camera["last_frame_timestamp"] is None
    assert camera["last_detection_timestamp"] is None


def test_status_with_no_cameras_is_empty_not_an_error(api_client):
    body = api_client.get("/api/ai/status").json()
    assert body["cameras"] == [] and body["cameras_total"] == 0


# --------------------------------------------------------- when AI is running
def test_camera_status_reports_live_runtime_values(api_client, api_camera):
    running(api_camera["camera_id"])
    body = api_client.get(f"/api/cameras/{api_camera['camera_id']}/ai-status").json()
    assert body["connected"] is True and body["ai_running"] is True
    assert body["connection_state"] == "online"
    assert body["person_count"] == 7 and body["active_tracks"] == 8
    assert body["processing_fps"] == 4.8 and body["capture_fps"] == 24.9
    assert body["frames_processed"] == 120
    assert body["last_frame_timestamp"].startswith("2026-09-20T12:00:10")
    assert body["last_detection_timestamp"].startswith("2026-09-20T12:00:09")
    assert body["tracker_session"] == "abc123def456"


def test_overall_status_counts_running_cameras(api_client, api_camera):
    api_client.post("/api/cameras", json={"camera_id": "ANN-HALL-01", "camera_name": "Hall"})
    running(api_camera["camera_id"])
    manager_status.set(ManagerState.RUNNING, model_loaded=True, model_name="yolov8n.pt", device="cpu")

    body = api_client.get("/api/ai/status").json()
    assert body["state"] == "running" and body["model_loaded"] is True
    assert body["model_name"] == "yolov8n.pt" and body["device"] == "cpu"
    assert body["cameras_total"] == 2 and body["cameras_running"] == 1
    by_id = {c["camera_id"]: c for c in body["cameras"]}
    assert by_id["ANN-ENT-01"]["ai_running"] is True
    assert by_id["ANN-HALL-01"]["ai_running"] is False


def test_a_disconnected_camera_reports_unknown_counts(api_client, api_camera):
    runtime = running(api_camera["camera_id"])
    runtime.update(connected=False, connection_state=Connection.OFFLINE, person_count=None,
                   active_tracks=None, last_error="read_timeout")
    body = api_client.get(f"/api/cameras/{api_camera['camera_id']}/ai-status").json()
    assert body["connected"] is False and body["ai_running"] is True
    assert body["connection_state"] == "offline"
    assert body["person_count"] is None
    assert body["last_error"] == "read_timeout"


def test_model_problem_is_visible(api_client, api_camera):
    manager_status.set(ManagerState.MODEL_UNAVAILABLE, detail="AI_MODEL_PATH is not set.")
    body = api_client.get("/api/ai/status").json()
    assert body["state"] == "model_unavailable"
    assert body["detail"] == "AI_MODEL_PATH is not set."
    assert body["model_loaded"] is False


def test_not_owner_state_is_visible(api_client):
    manager_status.set(ManagerState.NOT_OWNER, detail="another process already runs the AI pipeline")
    assert api_client.get("/api/ai/status").json()["state"] == "not_owner"


# -------------------------------------------------------------------- errors
def test_unknown_camera_returns_404(api_client):
    response = api_client.get("/api/cameras/NO-SUCH-CAM/ai-status")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_ai_status_route_does_not_collide_with_camera_routes(api_client, api_camera):
    assert api_client.get("/api/cameras/status").status_code == 200
    assert api_client.get(f"/api/cameras/{api_camera['camera_id']}").status_code == 200
    assert api_client.get(f"/api/cameras/{api_camera['camera_id']}/events").status_code == 200


def test_status_endpoints_are_read_only(api_client):
    paths = api_client.get("/openapi.json").json()["paths"]
    assert set(paths["/api/ai/status"]) == {"get"}
    assert set(paths["/api/cameras/{camera_id}/ai-status"]) == {"get"}


def test_status_endpoints_are_tagged_for_the_docs(api_client):
    paths = api_client.get("/openapi.json").json()["paths"]
    assert "AI Runtime" in paths["/api/ai/status"]["get"]["tags"]
    assert "AI Runtime" in paths["/api/cameras/{camera_id}/ai-status"]["get"]["tags"]


# ------------------------------------------------------------------- secrets
def test_no_rtsp_url_or_password_in_any_ai_response(api_client, api_camera):
    """api_camera was created with rtsp://admin:hunter2@10.0.0.5:554/stream."""
    running(api_camera["camera_id"])
    for path in ("/api/ai/status", f"/api/cameras/{api_camera['camera_id']}/ai-status"):
        text = api_client.get(path).text
        assert "hunter2" not in text
        assert "rtsp://" not in text
        assert "10.0.0.5" not in text
        assert "rtsp_url" not in text


def test_last_error_is_scrubbed_even_if_a_url_slipped_into_it(api_client, api_camera):
    runtime = running(api_camera["camera_id"])
    runtime.update(last_error="failed rtsp://admin:hunter2@10.0.0.5/s")
    for path in ("/api/ai/status", f"/api/cameras/{api_camera['camera_id']}/ai-status"):
        assert "hunter2" not in api_client.get(path).text


def test_manager_detail_is_scrubbed(api_client):
    manager_status.set(ManagerState.MODEL_UNAVAILABLE, detail="bad rtsp://u:leaked@h/x")
    assert "leaked" not in api_client.get("/api/ai/status").text


def test_model_directory_is_not_disclosed(api_client):
    manager_status.set(ManagerState.RUNNING, model_loaded=True, model_name="yolov8n.pt", device="cpu")
    text = api_client.get("/api/ai/status").text
    assert "/" not in api_client.get("/api/ai/status").json()["model_name"]
    assert "/home" not in text and "/tmp" not in text


def test_database_credentials_never_appear(api_client, api_camera, settings):
    from urllib.parse import urlsplit

    password = urlsplit(settings.database_url).password
    assert password
    running(api_camera["camera_id"])
    for path in ("/api/ai/status", f"/api/cameras/{api_camera['camera_id']}/ai-status"):
        assert password not in api_client.get(path).text
