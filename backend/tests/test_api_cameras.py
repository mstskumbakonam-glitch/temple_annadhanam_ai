"""Camera API tests."""

import pytest

pytestmark = pytest.mark.db


def test_create_camera(api_client):
    response = api_client.post(
        "/api/cameras", json={"camera_id": "ANN-HALL-09", "camera_name": "Hall 9"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["camera_id"] == "ANN-HALL-09"
    assert body["status"] == "OFFLINE"
    assert body["enabled"] is True


def test_camera_code_is_uppercased(api_client):
    response = api_client.post(
        "/api/cameras", json={"camera_id": "ann-low-01", "camera_name": "Lower"}
    )
    assert response.status_code == 201
    assert response.json()["camera_id"] == "ANN-LOW-01"


def test_get_camera(api_client, api_camera):
    response = api_client.get(f"/api/cameras/{api_camera['camera_id']}")
    assert response.status_code == 200
    assert response.json()["camera_name"] == "Entrance"


def test_get_unknown_camera_returns_404(api_client):
    response = api_client.get("/api/cameras/NO-SUCH-CAM")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_list_cameras(api_client, api_camera):
    response = api_client.get("/api/cameras")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert body["page"] == 1
    assert any(c["camera_id"] == api_camera["camera_id"] for c in body["items"])


def test_update_camera(api_client, api_camera):
    response = api_client.put(
        f"/api/cameras/{api_camera['camera_id']}",
        json={"camera_name": "Front Entrance", "status": "ONLINE", "fps": 24.5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["camera_name"] == "Front Entrance"
    assert body["status"] == "ONLINE"
    assert body["fps"] == 24.5


def test_disabling_camera_sets_disabled_status(api_client, api_camera):
    response = api_client.put(
        f"/api/cameras/{api_camera['camera_id']}", json={"enabled": False}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "DISABLED"


def test_delete_camera(api_client, api_camera):
    code = api_camera["camera_id"]
    assert api_client.delete(f"/api/cameras/{code}").status_code == 204
    assert api_client.get(f"/api/cameras/{code}").status_code == 404


def test_duplicate_camera_id_returns_409(api_client, api_camera):
    response = api_client.post(
        "/api/cameras",
        json={"camera_id": api_camera["camera_id"], "camera_name": "Duplicate"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


def test_rtsp_password_is_never_returned(api_client, api_camera):
    """The stored password must not appear in any camera response."""
    for response in (
        api_client.get(f"/api/cameras/{api_camera['camera_id']}"),
        api_client.get("/api/cameras"),
        api_client.get("/api/cameras/status"),
    ):
        assert "hunter2" not in response.text
    detail = api_client.get(f"/api/cameras/{api_camera['camera_id']}").json()
    assert detail["rtsp_url_masked"] == "rtsp://admin:***@10.0.0.5:554/stream"
    assert "rtsp_url" not in detail


def test_camera_status_endpoint_is_not_treated_as_a_camera_code(api_client, api_camera):
    """/api/cameras/status must not be captured by /api/cameras/{camera_id}."""
    response = api_client.get("/api/cameras/status")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_invalid_rtsp_scheme_returns_422(api_client):
    response = api_client.post(
        "/api/cameras",
        json={"camera_id": "BAD-URL-01", "camera_name": "Bad", "rtsp_url": "ftp://x/y"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


def test_missing_camera_name_returns_422(api_client):
    response = api_client.post("/api/cameras", json={"camera_id": "NO-NAME-01"})
    assert response.status_code == 422
