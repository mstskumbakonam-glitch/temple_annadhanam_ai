"""Seat, seat status and seat history API tests."""

import pytest

pytestmark = pytest.mark.db


def test_create_seat(api_client, api_camera, hall_factory):
    hall_factory("MAIN")
    response = api_client.post(
        "/api/seats",
        json={
            "seat_id": "S02",
            "hall_id": "MAIN",
            "camera_id": api_camera["camera_id"],
            "polygon_points": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["seat_id"] == "S02"
    assert body["camera_id"] == api_camera["camera_id"]
    assert len(body["polygon_points"]) == 3


def test_get_seat(api_client, api_seat):
    response = api_client.get(f"/api/seats/{api_seat['seat_id']}?hall_id=MAIN")
    assert response.status_code == 200
    assert response.json()["seat_label"] == "Seat 01"


def test_get_unknown_seat_returns_404(api_client):
    assert api_client.get("/api/seats/S99").status_code == 404


def test_list_seats(api_client, api_seat):
    body = api_client.get("/api/seats").json()
    assert body["total"] >= 1


def test_update_seat(api_client, api_seat):
    response = api_client.put(
        f"/api/seats/{api_seat['seat_id']}?hall_id=MAIN",
        json={"seat_label": "Front row", "enabled": False},
    )
    assert response.status_code == 200
    assert response.json()["seat_label"] == "Front row"
    assert response.json()["enabled"] is False


def test_delete_seat(api_client, api_seat):
    assert api_client.delete(f"/api/seats/{api_seat['seat_id']}?hall_id=MAIN").status_code == 204
    assert api_client.get(f"/api/seats/{api_seat['seat_id']}?hall_id=MAIN").status_code == 404


def test_duplicate_seat_in_same_hall_returns_409(api_client, api_seat):
    response = api_client.post("/api/seats", json={"seat_id": "S01", "hall_id": "MAIN"})
    assert response.status_code == 409


def test_same_seat_id_allowed_in_different_halls(api_client, api_seat, hall_factory):
    hall_factory("HALL-B")
    response = api_client.post("/api/seats", json={"seat_id": "S01", "hall_id": "HALL-B"})
    assert response.status_code == 201
    assert response.json()["hall_id"] == "HALL-B"


def test_polygon_needs_at_least_three_points(api_client):
    response = api_client.post(
        "/api/seats",
        json={"seat_id": "S50", "polygon_points": [{"x": 1, "y": 1}, {"x": 2, "y": 2}]},
    )
    assert response.status_code == 422


def test_seat_with_unknown_camera_returns_404(api_client):
    response = api_client.post(
        "/api/seats", json={"seat_id": "S51", "camera_id": "NO-CAM-01"}
    )
    assert response.status_code == 404


def test_seat_status_reports_empty_seat(api_client, api_seat):
    rows = api_client.get("/api/seats/status").json()
    row = next(r for r in rows if r["seat_id"] == "S01")
    assert row["status"] == "EMPTY"
    assert row["visitor_code"] is None
    assert row["staff_code"] is None
    assert row["occupied_at"] is None


def test_seat_status_reports_visitor_occupancy(api_client, api_seat, session):
    from sqlalchemy import select

    from app.models import Seat, SeatOccupancy, Visitor

    seat = session.scalar(select(Seat).where(Seat.seat_id == "S01", Seat.hall_id == "MAIN"))
    visitor = Visitor()
    session.add(visitor)
    session.flush()
    session.add(
        SeatOccupancy(seat_id=seat.id, person_type="VISITOR", visitor_id=visitor.id)
    )
    session.flush()

    row = next(r for r in api_client.get("/api/seats/status").json() if r["seat_id"] == "S01")
    assert row["status"] == "OCCUPIED"
    assert row["person_type"] == "VISITOR"
    assert row["visitor_code"] == visitor.visitor_code
    assert row["staff_code"] is None
    assert row["duration_seconds"] >= 0


def test_seat_status_reports_staff_occupancy(api_client, api_seat, api_staff, session):
    from sqlalchemy import select

    from app.models import Seat, SeatOccupancy, Staff

    seat = session.scalar(select(Seat).where(Seat.seat_id == "S01", Seat.hall_id == "MAIN"))
    staff = session.scalar(select(Staff).where(Staff.staff_code == api_staff["staff_code"]))
    session.add(SeatOccupancy(seat_id=seat.id, person_type="STAFF", staff_id=staff.id))
    session.flush()

    row = next(r for r in api_client.get("/api/seats/status").json() if r["seat_id"] == "S01")
    assert row["status"] == "OCCUPIED"
    assert row["person_type"] == "STAFF"
    assert row["staff_code"] == api_staff["staff_code"]
    assert row["visitor_code"] is None


def test_seat_status_route_not_treated_as_seat_code(api_client):
    assert isinstance(api_client.get("/api/seats/status").json(), list)


def test_seat_history_is_empty_initially(api_client):
    body = api_client.get("/api/seats/history").json()
    assert body["items"] == []
    assert body["total"] == 0


def test_seat_history_returns_released_periods(api_client, api_seat, session):
    from sqlalchemy import select

    from app.models import Seat, SeatOccupancy, Visitor
    from app.utils.time import utc_now

    seat = session.scalar(select(Seat).where(Seat.seat_id == "S01", Seat.hall_id == "MAIN"))
    visitor = Visitor()
    session.add(visitor)
    session.flush()
    session.add(
        SeatOccupancy(
            seat_id=seat.id,
            person_type="VISITOR",
            visitor_id=visitor.id,
            status="RELEASED",
            released_at=utc_now(),
            duration_seconds=300,
        )
    )
    session.flush()

    body = api_client.get("/api/seats/history").json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["seat_id"] == "S01"
    assert row["person_type"] == "VISITOR"
    assert row["duration_seconds"] == 300
    assert row["visitor_code"] == visitor.visitor_code


def test_seat_history_person_type_filter(api_client, api_seat, session):
    from sqlalchemy import select

    from app.models import Seat, SeatOccupancy, Visitor

    seat = session.scalar(select(Seat).where(Seat.seat_id == "S01", Seat.hall_id == "MAIN"))
    visitor = Visitor()
    session.add(visitor)
    session.flush()
    session.add(SeatOccupancy(seat_id=seat.id, person_type="VISITOR", visitor_id=visitor.id))
    session.flush()

    assert api_client.get("/api/seats/history?person_type=VISITOR").json()["total"] == 1
    assert api_client.get("/api/seats/history?person_type=STAFF").json()["total"] == 0


def test_seat_in_unknown_hall_is_404(api_client):
    response = api_client.post("/api/seats", json={"seat_id": "S01", "hall_id": "NO-SUCH-HALL"})
    assert response.status_code == 404
    assert "Create the hall first" in response.json()["detail"]
