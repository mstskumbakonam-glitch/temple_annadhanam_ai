"""Dashboard, hall occupancy, pagination and error-handling tests."""

import pytest

pytestmark = pytest.mark.db

SUMMARY_KEYS = {
    "total_cameras",
    "online_cameras",
    "offline_cameras",
    "current_visitors",
    "today_entries",
    "today_exits",
    "staff_present",
    "total_seats",
    "occupied_seats",
    "empty_seats",
    "occupancy_percentage",
}


# ------------------------------------------------------------------ summary
def test_summary_on_empty_database_is_all_zero(api_client):
    """With no data the API reports zeros rather than failing or inventing numbers."""
    body = api_client.get("/api/dashboard/summary").json()
    assert set(body) == SUMMARY_KEYS
    assert all(body[key] == 0 for key in SUMMARY_KEYS)


def test_summary_counts_cameras(api_client, api_camera):
    body = api_client.get("/api/dashboard/summary").json()
    assert body["total_cameras"] == 1
    assert body["online_cameras"] == 0
    assert body["offline_cameras"] == 1

    api_client.put(f"/api/cameras/{api_camera['camera_id']}", json={"status": "ONLINE"})
    body = api_client.get("/api/dashboard/summary").json()
    assert body["online_cameras"] == 1
    assert body["offline_cameras"] == 0


def test_summary_counts_seats_and_occupancy(api_client, api_seat, session):
    from sqlalchemy import select

    from app.models import Seat, SeatOccupancy, Visitor

    api_client.post("/api/seats", json={"seat_id": "S02", "hall_id": "MAIN"})

    body = api_client.get("/api/dashboard/summary").json()
    assert body["total_seats"] == 2
    assert body["occupied_seats"] == 0
    assert body["empty_seats"] == 2
    assert body["occupancy_percentage"] == 0.0

    seat = session.scalar(select(Seat).where(Seat.seat_id == "S01", Seat.hall_id == "MAIN"))
    visitor = Visitor()
    session.add(visitor)
    session.flush()
    session.add(SeatOccupancy(seat_id=seat.id, person_type="VISITOR", visitor_id=visitor.id))
    session.flush()

    body = api_client.get("/api/dashboard/summary").json()
    assert body["occupied_seats"] == 1
    assert body["empty_seats"] == 1
    assert body["occupancy_percentage"] == 50.0


def test_summary_counts_current_visitors_and_today(api_client, session):
    from app.models import Visitor
    from app.utils.time import utc_now

    session.add(Visitor(status="INSIDE", entry_time=utc_now()))
    session.add(Visitor(status="EXITED", entry_time=utc_now(), exit_time=utc_now()))
    session.flush()

    body = api_client.get("/api/dashboard/summary").json()
    assert body["current_visitors"] == 1
    assert body["today_entries"] == 2
    assert body["today_exits"] == 1


def test_summary_counts_staff_present(api_client, api_staff, session):
    from sqlalchemy import select

    from app.models import Staff, StaffAttendance

    assert api_client.get("/api/dashboard/summary").json()["staff_present"] == 0

    staff = session.scalar(select(Staff).where(Staff.staff_code == api_staff["staff_code"]))
    session.add(StaffAttendance(staff_id=staff.id))
    session.flush()

    assert api_client.get("/api/dashboard/summary").json()["staff_present"] == 1


# ---------------------------------------------------------- hall occupancy
def test_hall_with_no_seats_returns_zero_not_division_error(api_client):
    body = api_client.get("/api/halls/EMPTY-HALL/occupancy").json()
    assert body["total_seats"] == 0
    assert body["occupied_seats"] == 0
    assert body["empty_seats"] == 0
    assert body["occupancy_percentage"] == 0.0


def test_hall_occupancy_percentage(api_client, api_camera):
    for index in range(1, 5):
        api_client.post("/api/seats", json={"seat_id": f"S{index:02d}", "hall_id": "HALL-X"})

    body = api_client.get("/api/halls/HALL-X/occupancy").json()
    assert body["total_seats"] == 4
    assert body["occupancy_percentage"] == 0.0


def test_hall_occupancy_is_scoped_to_one_hall(api_client):
    api_client.post("/api/seats", json={"seat_id": "S01", "hall_id": "HALL-A"})
    api_client.post("/api/seats", json={"seat_id": "S01", "hall_id": "HALL-B"})
    api_client.post("/api/seats", json={"seat_id": "S02", "hall_id": "HALL-B"})

    assert api_client.get("/api/halls/HALL-A/occupancy").json()["total_seats"] == 1
    assert api_client.get("/api/halls/HALL-B/occupancy").json()["total_seats"] == 2


# ------------------------------------------------------------- pagination
def test_pagination_defaults(api_client):
    body = api_client.get("/api/cameras").json()
    assert body["page"] == 1
    assert body["page_size"] == 50


def test_pagination_pages_through_results(api_client):
    for index in range(1, 8):
        api_client.post(
            "/api/cameras", json={"camera_id": f"PAGE-{index:02d}", "camera_name": f"C{index}"}
        )

    first = api_client.get("/api/cameras?page=1&page_size=3").json()
    assert first["total"] == 7
    assert len(first["items"]) == 3

    last = api_client.get("/api/cameras?page=3&page_size=3").json()
    assert len(last["items"]) == 1
    assert last["total"] == 7

    seen = {c["camera_id"] for c in first["items"]} | {c["camera_id"] for c in last["items"]}
    assert len(seen) == 4  # no overlap between pages


def test_page_size_above_maximum_is_rejected(api_client):
    response = api_client.get("/api/cameras?page_size=201")
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


def test_maximum_page_size_is_accepted(api_client):
    assert api_client.get("/api/cameras?page_size=200").status_code == 200


def test_page_zero_is_rejected(api_client):
    assert api_client.get("/api/cameras?page=0").status_code == 422


def test_empty_page_beyond_results_is_not_an_error(api_client, api_camera):
    body = api_client.get("/api/cameras?page=50").json()
    assert body["items"] == []
    assert body["total"] == 1


# ---------------------------------------------------------- error handling
def test_errors_have_consistent_shape(api_client):
    body = api_client.get("/api/cameras/NOPE-99").json()
    assert set(body) >= {"detail", "code"}
    assert isinstance(body["detail"], str)


def test_errors_do_not_leak_sql_or_stack_traces(api_client, api_camera):
    response = api_client.post(
        "/api/cameras",
        json={"camera_id": api_camera["camera_id"], "camera_name": "Dup"},
    )
    assert response.status_code == 409
    text = response.text.lower()
    for leak in ("traceback", "sqlalchemy", "psycopg", "insert into", "duplicate key"):
        assert leak not in text


def test_validation_error_reports_the_offending_field(api_client):
    response = api_client.post("/api/cameras", json={"camera_id": "OK-01"})
    assert response.status_code == 422
    assert "camera_name" in response.text


# ------------------------------------------------------------ transactions
def test_failed_create_rolls_back_and_session_stays_usable(api_client, api_camera):
    """A 409 must not poison the session: the next request still works."""
    assert (
        api_client.post(
            "/api/cameras",
            json={"camera_id": api_camera["camera_id"], "camera_name": "Dup"},
        ).status_code
        == 409
    )
    assert api_client.get("/api/cameras").status_code == 200
    assert (
        api_client.post(
            "/api/cameras", json={"camera_id": "AFTER-FAIL-01", "camera_name": "Works"}
        ).status_code
        == 201
    )


def test_conflicting_create_does_not_persist_a_row(api_client, api_camera):
    before = api_client.get("/api/cameras").json()["total"]
    api_client.post(
        "/api/cameras", json={"camera_id": api_camera["camera_id"], "camera_name": "Dup"}
    )
    assert api_client.get("/api/cameras").json()["total"] == before


# ------------------------------------------------------------------- docs
def test_openapi_docs_are_available(api_client):
    assert api_client.get("/docs").status_code == 200
    assert api_client.get("/redoc").status_code == 200
    assert api_client.get("/openapi.json").status_code == 200


def test_endpoints_are_tagged(api_client):
    paths = api_client.get("/openapi.json").json()["paths"]
    expected = {"Cameras", "Visitors", "Staff", "Attendance", "Seats", "Events", "Dashboard"}
    used = {
        tag
        for methods in paths.values()
        for op in methods.values()
        for tag in op.get("tags", [])
    }
    assert expected <= used
