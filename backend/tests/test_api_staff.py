"""Staff and attendance API tests."""

import pytest

pytestmark = pytest.mark.db


def test_create_staff_assigns_code(api_client):
    response = api_client.post("/api/staff", json={"staff_name": "Meena"})
    assert response.status_code == 201
    body = response.json()
    assert body["staff_code"].startswith("STAFF-")
    assert body["active"] is True


def test_get_staff(api_client, api_staff):
    response = api_client.get(f"/api/staff/{api_staff['staff_code']}")
    assert response.status_code == 200
    assert response.json()["staff_name"] == "Ravi Kumar"


def test_get_unknown_staff_returns_404(api_client):
    assert api_client.get("/api/staff/STAFF-999").status_code == 404


def test_list_staff(api_client, api_staff):
    body = api_client.get("/api/staff").json()
    assert body["total"] >= 1
    assert any(s["staff_code"] == api_staff["staff_code"] for s in body["items"])


def test_update_staff(api_client, api_staff):
    response = api_client.put(
        f"/api/staff/{api_staff['staff_code']}",
        json={"department": "Kitchen", "active": False},
    )
    assert response.status_code == 200
    assert response.json()["department"] == "Kitchen"
    assert response.json()["active"] is False


def test_delete_staff(api_client, api_staff):
    code = api_staff["staff_code"]
    assert api_client.delete(f"/api/staff/{code}").status_code == 204
    assert api_client.get(f"/api/staff/{code}").status_code == 404


def test_duplicate_employee_code_returns_409(api_client, api_staff):
    response = api_client.post(
        "/api/staff", json={"staff_name": "Other", "employee_code": "EMP-100"}
    )
    assert response.status_code == 409


def test_face_embedding_never_exposed(api_client, api_staff, session):
    """Even when an embedding exists, no staff endpoint returns it."""
    from sqlalchemy import select

    from app.models import Staff

    staff = session.scalar(
        select(Staff).where(Staff.staff_code == api_staff["staff_code"])
    )
    staff.face_embedding = b"\x01\x02secretvector"
    session.flush()

    for response in (
        api_client.get(f"/api/staff/{api_staff['staff_code']}"),
        api_client.get("/api/staff"),
    ):
        assert response.status_code == 200
        assert "face_embedding" not in response.text
        assert "secretvector" not in response.text


def test_attendance_list_is_empty_initially(api_client):
    body = api_client.get("/api/staff/attendance").json()
    assert body["items"] == []
    assert body["total"] == 0


def test_attendance_route_not_treated_as_staff_code(api_client):
    """/api/staff/attendance must not resolve to /api/staff/{staff_code}."""
    response = api_client.get("/api/staff/attendance")
    assert response.status_code == 200
    assert "items" in response.json()


def test_attendance_records_are_returned(api_client, api_staff, api_camera, session):
    from sqlalchemy import select

    from app.models import Camera, Staff, StaffAttendance

    staff = session.scalar(select(Staff).where(Staff.staff_code == api_staff["staff_code"]))
    camera = session.scalar(select(Camera).where(Camera.camera_id == api_camera["camera_id"]))
    session.add(StaffAttendance(staff_id=staff.id, camera_id=camera.id))
    session.flush()

    body = api_client.get("/api/staff/attendance").json()
    assert body["total"] == 1
    record = body["items"][0]
    assert record["staff_code"] == api_staff["staff_code"]
    assert record["staff_name"] == "Ravi Kumar"
    assert record["camera_id"] == api_camera["camera_id"]
    assert record["status"] == "PRESENT"


def test_attendance_status_filter(api_client, api_staff, session):
    from sqlalchemy import select

    from app.models import Staff, StaffAttendance

    staff = session.scalar(select(Staff).where(Staff.staff_code == api_staff["staff_code"]))
    session.add(StaffAttendance(staff_id=staff.id))
    session.flush()

    assert api_client.get("/api/staff/attendance?status=PRESENT").json()["total"] == 1
    assert api_client.get("/api/staff/attendance?status=EXITED").json()["total"] == 0


def test_per_staff_attendance_endpoint(api_client, api_staff, session):
    from sqlalchemy import select

    from app.models import Staff, StaffAttendance

    staff = session.scalar(select(Staff).where(Staff.staff_code == api_staff["staff_code"]))
    session.add(StaffAttendance(staff_id=staff.id))
    session.flush()

    response = api_client.get(f"/api/staff/{api_staff['staff_code']}/attendance")
    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_per_staff_attendance_unknown_staff_returns_404(api_client):
    assert api_client.get("/api/staff/STAFF-404/attendance").status_code == 404
