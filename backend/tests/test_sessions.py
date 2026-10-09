"""Annadhanam session scheduling: time zone, conflicts, capacity, status rules."""

from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.services.session_service import local_today

pytestmark = pytest.mark.db

FUTURE = "2030-03-15"


@pytest.fixture
def halls(api_client):
    api_client.post("/api/temples", json={"temple_code": "T1", "name": "Temple One"})
    for code in ("H1", "H2"):
        api_client.post("/api/halls", json={"hall_code": code, "temple_code": "T1", "name": f"Hall {code}"})
    api_client.post("/api/halls/H1/seats/generate", json={"rows": 5, "columns": 10})   # 50 seats
    return ("H1", "H2")


def make(client, hall="H1", day=FUTURE, start="11:00", end="13:00", **kw):
    body = {"hall_code": hall, "name": kw.pop("name", "Noon annadhanam"), "meal_type": kw.pop("meal_type", "LUNCH"),
            "session_date": day, "start_time": start, "end_time": end, **kw}
    return client.post("/api/sessions", json=body)


def test_create_converts_local_time_to_utc(api_client, halls):
    r = make(api_client, expected_devotees=40, notes="Pournami")
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["starts_at"].startswith("2030-03-15T05:30:00")      # 11:00 IST = 05:30 UTC
    assert s["start_time"] == "11:00:00" and s["end_time"] == "13:00:00"
    assert s["temple_code"] == "T1" and s["hall_capacity"] == 50
    assert s["status"] == "SCHEDULED" and s["timing"] == "upcoming" and s["capacity_warning"] is None


def test_overlap_in_same_hall_is_rejected(api_client, halls):
    assert make(api_client).status_code == 201
    r = make(api_client, start="12:30", end="14:00", name="Second")
    assert r.status_code == 409 and "overlapping" in r.json()["detail"]


def test_back_to_back_and_other_halls_are_fine(api_client, halls):
    assert make(api_client).status_code == 201
    assert make(api_client, start="13:00", end="14:00").status_code == 201    # starts when the other ends
    assert make(api_client, hall="H2").status_code == 201                     # same time, other hall


def test_cancelled_session_frees_its_slot(api_client, halls):
    first = make(api_client).json()
    r = api_client.post(f"/api/sessions/{first['id']}/cancel", json={"reason": "Temple festival change"})
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    assert make(api_client).status_code == 201
    assert api_client.post(f"/api/sessions/{first['id']}/cancel", json={"reason": "again"}).status_code == 409
    assert api_client.put(f"/api/sessions/{first['id']}", json={"name": "x"}).status_code == 409


def test_moving_a_session_onto_another_is_rejected(api_client, halls):
    make(api_client)
    other = make(api_client, start="15:00", end="16:00").json()
    r = api_client.put(f"/api/sessions/{other['id']}", json={"start_time": "12:00"})
    assert r.status_code == 409
    r = api_client.put(f"/api/sessions/{other['id']}", json={"hall_code": "H2", "start_time": "12:00"})
    assert r.status_code == 200 and r.json()["hall_code"] == "H2"


def test_capacity_warning(api_client, halls):
    r = make(api_client, expected_devotees=120).json()
    assert "seats 50" in r["capacity_warning"] and "3 sittings" in r["capacity_warning"]
    r = make(api_client, hall="H2").json()
    assert r["capacity_warning"] == "This hall has no seats in service."


@pytest.mark.parametrize("bad", [
    {"start": "13:00", "end": "11:00"},
    {"start": "11:00", "end": "11:00"},
    {"meal_type": "BRUNCH"},
    {"expected_devotees": -1},
])
def test_validation(api_client, halls, bad):
    assert make(api_client, **bad).status_code == 422


def test_responsible_staff_must_exist_and_be_active(api_client, halls):
    assert make(api_client, responsible_staff_code="STAFF-999").status_code == 404
    staff = api_client.post("/api/staff", json={"staff_name": "Lakshmi", "active": False}).json()
    assert make(api_client, responsible_staff_code=staff["staff_code"]).status_code == 422
    api_client.put(f"/api/staff/{staff['staff_code']}", json={"active": True})
    r = make(api_client, responsible_staff_code=staff["staff_code"])
    assert r.status_code == 201 and r.json()["responsible_staff_name"] == "Lakshmi"


def test_status_flow_and_actual_attendance(api_client, halls):
    past = (local_today(get_settings().site_timezone) - timedelta(days=2)).isoformat()
    s = make(api_client, day=past, expected_devotees=40).json()
    assert s["timing"] == "past"
    r = api_client.put(f"/api/sessions/{s['id']}", json={"status": "COMPLETED", "actual_devotees": 47})
    assert r.status_code == 200 and r.json()["actual_devotees"] == 47
    # completed: only actual_devotees and notes may change
    assert api_client.put(f"/api/sessions/{s['id']}", json={"notes": "rain"}).status_code == 200
    assert api_client.put(f"/api/sessions/{s['id']}", json={"start_time": "10:00"}).status_code == 409
    assert api_client.put(f"/api/sessions/{s['id']}", json={"status": "SCHEDULED"}).status_code == 409


def test_cannot_complete_before_start(api_client, halls):
    s = make(api_client).json()
    assert api_client.put(f"/api/sessions/{s['id']}", json={"status": "COMPLETED"}).status_code == 422


def test_calendar_filters(api_client, halls):
    make(api_client, day="2030-03-15")
    make(api_client, day="2030-03-16", meal_type="DINNER", start="19:00", end="20:30")
    make(api_client, hall="H2", day="2030-03-16")
    r = api_client.get("/api/sessions", params={"start_date": "2030-03-16", "end_date": "2030-03-16"}).json()
    assert r["total"] == 2
    r = api_client.get("/api/sessions", params={"hall_code": "H2"}).json()
    assert [s["hall_code"] for s in r["items"]] == ["H2"]
    r = api_client.get("/api/sessions", params={"timing": "upcoming", "temple_code": "T1"}).json()
    assert r["total"] == 3


def test_database_exclusion_constraint_is_the_final_guard(session, hall_factory):
    """Even bypassing the service, PostgreSQL refuses an overlapping session."""
    from datetime import date, datetime, timezone

    from app.models import AnnadhanamSession

    h = hall_factory("DBX")
    t = lambda hh: datetime(2030, 1, 1, hh, tzinfo=timezone.utc)  # noqa: E731
    session.add(AnnadhanamSession(hall_id=h.id, name="A", meal_type="LUNCH", session_date=date(2030, 1, 1),
                                  starts_at=t(5), ends_at=t(7)))
    session.flush()
    session.add(AnnadhanamSession(hall_id=h.id, name="B", meal_type="LUNCH", session_date=date(2030, 1, 1),
                                  starts_at=t(6), ends_at=t(8)))
    with pytest.raises(IntegrityError):
        session.flush()
