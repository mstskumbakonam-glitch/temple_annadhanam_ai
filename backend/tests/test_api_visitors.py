"""Visitor API tests. Visitors are created directly in the database here,
because the AI pipeline that normally creates them arrives in a later phase."""

import pytest

pytestmark = pytest.mark.db


@pytest.fixture
def stored_visitor(session, api_camera):
    from sqlalchemy import select

    from app.models import Camera, Visitor
    from app.utils.time import utc_now

    camera = session.scalar(select(Camera).where(Camera.camera_id == api_camera["camera_id"]))
    visitor = Visitor(
        camera_id=camera.id, tracking_id=17, entry_time=utc_now(), status="INSIDE"
    )
    session.add(visitor)
    session.flush()
    session.refresh(visitor)
    return visitor


def test_visitor_list_is_empty_initially(api_client):
    body = api_client.get("/api/visitors").json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["page"] == 1
    assert body["page_size"] == 50


def test_list_visitors(api_client, stored_visitor):
    body = api_client.get("/api/visitors").json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["visitor_code"] == stored_visitor.visitor_code
    assert item["tracking_id"] == 17
    assert item["status"] == "INSIDE"


def test_visitor_response_exposes_codes_not_row_ids(api_client, stored_visitor, api_camera):
    item = api_client.get("/api/visitors").json()["items"][0]
    assert item["camera_id"] == api_camera["camera_id"]
    assert "id" not in item


def test_get_visitor(api_client, stored_visitor):
    response = api_client.get(f"/api/visitors/{stored_visitor.visitor_code}")
    assert response.status_code == 200
    assert response.json()["visitor_code"] == stored_visitor.visitor_code


def test_get_unknown_visitor_returns_404(api_client):
    response = api_client.get("/api/visitors/VIS-999999")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_current_visitors(api_client, stored_visitor):
    body = api_client.get("/api/visitors/current").json()
    assert body["total"] == 1

    stored_visitor.status = "EXITED"
    assert api_client.get("/api/visitors/current").json()["total"] == 0


def test_today_visitors(api_client, stored_visitor):
    assert api_client.get("/api/visitors/today").json()["total"] == 1


def test_current_and_today_are_not_treated_as_visitor_codes(api_client):
    for path in ("/api/visitors/current", "/api/visitors/today"):
        assert api_client.get(path).status_code == 200


def test_visitor_status_filter(api_client, stored_visitor):
    assert api_client.get("/api/visitors?status=INSIDE").json()["total"] == 1
    assert api_client.get("/api/visitors?status=EXITED").json()["total"] == 0


def test_visitor_camera_filter(api_client, stored_visitor, api_camera):
    assert api_client.get(f"/api/visitors?camera_id={api_camera['camera_id']}").json()["total"] == 1


def test_no_visitor_face_endpoints_exist(api_client):
    """Visitors are anonymous: nothing face-related may be exposed for them."""
    paths = api_client.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if "visitor" in p.lower() and "face" in p.lower()]
    assert not [p for p in paths if "embedding" in p.lower()]


def test_visitors_are_read_only(api_client):
    """No create/update/delete endpoints: visitors come from the AI pipeline."""
    paths = api_client.get("/openapi.json").json()["paths"]
    for path, methods in paths.items():
        if path.startswith("/api/visitors"):
            assert set(methods) <= {"get"}, f"{path} exposes {set(methods)}"


def test_visitor_events(api_client, stored_visitor, session, api_camera):
    from sqlalchemy import select

    from app.models import Camera, VisitorEvent

    camera = session.scalar(select(Camera).where(Camera.camera_id == api_camera["camera_id"]))
    session.add(
        VisitorEvent(
            visitor_id=stored_visitor.id,
            camera_id=camera.id,
            event_type="ENTERED",
            tracking_id=17,
            event_metadata={"line": "entry"},
        )
    )
    session.flush()

    body = api_client.get(f"/api/visitors/{stored_visitor.visitor_code}/events").json()
    assert body["total"] == 1
    event = body["items"][0]
    assert event["event_type"] == "ENTERED"
    assert event["metadata"]["line"] == "entry"
    assert event["camera_id"] == api_camera["camera_id"]


def test_visitor_events_unknown_visitor_returns_404(api_client):
    assert api_client.get("/api/visitors/VIS-888888/events").status_code == 404


def test_camera_events(api_client, api_camera, session):
    from sqlalchemy import select

    from app.models import Camera, CameraEvent

    camera = session.scalar(select(Camera).where(Camera.camera_id == api_camera["camera_id"]))
    session.add(
        CameraEvent(camera_id=camera.id, event_type="OFFLINE", fps=0.0, message="timeout")
    )
    session.flush()

    body = api_client.get(f"/api/cameras/{api_camera['camera_id']}/events").json()
    assert body["total"] == 1
    assert body["items"][0]["event_type"] == "OFFLINE"
    assert body["items"][0]["message"] == "timeout"


def test_camera_events_type_filter(api_client, api_camera, session):
    from sqlalchemy import select

    from app.models import Camera, CameraEvent

    camera = session.scalar(select(Camera).where(Camera.camera_id == api_camera["camera_id"]))
    session.add(CameraEvent(camera_id=camera.id, event_type="ONLINE"))
    session.flush()

    base = f"/api/cameras/{api_camera['camera_id']}/events"
    assert api_client.get(f"{base}?event_type=ONLINE").json()["total"] == 1
    assert api_client.get(f"{base}?event_type=ERROR").json()["total"] == 0


def test_camera_events_unknown_camera_returns_404(api_client):
    assert api_client.get("/api/cameras/NOPE-01/events").status_code == 404
