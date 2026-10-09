"""Confirmed seat status: transitions, counting rules, history and concurrency."""

import threading

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.db


@pytest.fixture
def hall(api_client):
    api_client.post("/api/temples", json={"temple_code": "T1", "name": "Temple One"})
    api_client.post("/api/halls", json={"hall_code": "H1", "temple_code": "T1", "name": "Hall One"})
    r = api_client.post("/api/halls/H1/seats/generate", json={"rows": 2, "columns": 5})
    assert r.json()["created"] == 10
    return "H1"


def set_status(client, seat, status, hall="H1", **kw):
    return client.post(f"/api/halls/{hall}/seats/{seat}/status", json={"status": status, **kw})


def counts(client, hall="H1"):
    return client.get(f"/api/halls/{hall}").json()["seats"]


# -------------------------------------------------------------- transitions
def test_occupy_and_release(api_client, hall):
    r = set_status(api_client, "S001", "OCCUPIED")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "OCCUPIED" and body["occupied_since"] is not None
    assert body["status_version"] == 2 and body["status_source"] == "MANUAL"
    r = set_status(api_client, "S001", "AVAILABLE")
    assert r.json()["status"] == "AVAILABLE" and r.json()["occupied_since"] is None


def test_the_same_seat_cannot_be_occupied_twice(api_client, hall):
    assert set_status(api_client, "S001", "OCCUPIED").status_code == 200
    r = set_status(api_client, "S001", "OCCUPIED")
    assert r.status_code == 409 and "already occupied" in r.json()["detail"]


def test_reservation_flow(api_client, hall):
    r = set_status(api_client, "S002", "RESERVED", reservation_ref="ELDERLY-1")
    assert r.status_code == 200 and r.json()["reservation_ref"] == "ELDERLY-1"
    r = set_status(api_client, "S002", "OCCUPIED")                       # reservation honoured
    assert r.json()["status"] == "OCCUPIED" and r.json()["reservation_ref"] is None


def test_reservation_fields_only_when_reserving(api_client, hall):
    assert set_status(api_client, "S001", "OCCUPIED", reservation_ref="X").status_code == 422


def test_reserve_for_a_session_must_match_the_hall(api_client, hall):
    api_client.post("/api/halls", json={"hall_code": "H2", "temple_code": "T1", "name": "Hall Two"})
    other = api_client.post("/api/sessions", json={
        "hall_code": "H2", "name": "Lunch", "meal_type": "LUNCH", "session_date": "2030-01-01",
        "start_time": "11:00", "end_time": "13:00"}).json()
    r = set_status(api_client, "S001", "RESERVED", session_id=other["id"])
    assert r.status_code == 422
    same = api_client.post("/api/sessions", json={
        "hall_code": "H1", "name": "Lunch", "meal_type": "LUNCH", "session_date": "2030-01-01",
        "start_time": "11:00", "end_time": "13:00"}).json()
    r = set_status(api_client, "S001", "RESERVED", session_id=same["id"])
    assert r.status_code == 200 and r.json()["reserved_session_id"] == same["id"]


@pytest.mark.parametrize("path,target", [
    (["OCCUPIED"], "OUT_OF_SERVICE"),        # release first
    (["OUT_OF_SERVICE"], "OCCUPIED"),        # return to service first
    (["OUT_OF_SERVICE"], "RESERVED"),
])
def test_invalid_transitions_are_rejected(api_client, hall, path, target):
    for step in path:
        assert set_status(api_client, "S003", step).status_code == 200
    assert set_status(api_client, "S003", target).status_code == 409


def test_stale_version_is_rejected(api_client, hall):
    seat = api_client.get("/api/halls/H1/seats").json()[0]
    assert set_status(api_client, seat["seat_id"], "RESERVED",
                      expected_version=seat["status_version"]).status_code == 200
    # a second operator still looking at the old version
    r = set_status(api_client, seat["seat_id"], "OCCUPIED", expected_version=seat["status_version"])
    assert r.status_code == 409 and "changed by someone else" in r.json()["detail"]


def test_disabled_seat_and_inactive_hall_cannot_change(api_client, hall):
    api_client.put("/api/seats/S010?hall_id=H1", json={"enabled": False})
    assert set_status(api_client, "S010", "OCCUPIED").status_code == 409
    api_client.put("/api/halls/H1", json={"active": False})
    assert set_status(api_client, "S001", "OCCUPIED").status_code == 409


def test_unknown_seat_or_hall_is_404(api_client, hall):
    assert set_status(api_client, "S999", "OCCUPIED").status_code == 404
    assert set_status(api_client, "S001", "OCCUPIED", hall="NOPE").status_code == 404


# ------------------------------------------------------------ counting rules
def test_counts_never_double_count(api_client, hall):
    for seat in ("S001", "S002", "S003"):
        set_status(api_client, seat, "OCCUPIED")
    for seat in ("S004", "S005"):
        set_status(api_client, seat, "RESERVED")
    set_status(api_client, "S006", "OUT_OF_SERVICE")
    api_client.put("/api/seats/S010?hall_id=H1", json={"enabled": False})   # removed entirely

    c = counts(api_client)
    assert c == {"installed": 9, "capacity": 8, "available": 3, "occupied": 3, "reserved": 2,
                 "out_of_service": 1, "occupancy_percentage": 37.5}
    assert c["available"] == c["capacity"] - c["occupied"] - c["reserved"]
    legacy = api_client.get("/api/halls/H1/occupancy").json()
    assert (legacy["total_seats"], legacy["occupied_seats"], legacy["reserved_seats"],
            legacy["empty_seats"]) == (8, 3, 2, 3)
    filtered = api_client.get("/api/halls/H1/seats", params={"status": "RESERVED"}).json()
    assert [s["seat_id"] for s in filtered] == ["S004", "S005"]


def test_history_records_each_change_with_duration(api_client, hall):
    set_status(api_client, "S001", "OCCUPIED", note="family of four")
    set_status(api_client, "S001", "AVAILABLE")
    page = api_client.get("/api/halls/H1/seats/history").json()
    assert page["total"] == 2
    latest, first = page["items"]
    assert (first["from_status"], first["to_status"], first["note"]) == ("AVAILABLE", "OCCUPIED", "family of four")
    assert latest["from_status"] == "OCCUPIED" and latest["previous_duration_seconds"] >= 0
    assert latest["actor_role"] == "admin"           # open dev mode acts as admin


def test_ai_confirmed_source_is_kept_distinct(api_client, hall):
    r = set_status(api_client, "S001", "OCCUPIED", source="AI_CONFIRMED")
    assert r.json()["status_source"] == "AI_CONFIRMED"


# -------------------------------------------------------------- concurrency
def test_simultaneous_occupy_requests_allow_exactly_one(migrated_database):
    """Eight operators press 'occupy' on one seat at the same instant, each through
    their own database connection and transaction (real commits, no test rollback)."""
    from app.models import AnnadhanamHall, Seat, Temple
    from app.schemas.management import SeatStatusChange
    from app.services import seat_status_service
    from app.services.exceptions import ConflictError

    factory = sessionmaker(bind=migrated_database, expire_on_commit=False)
    with factory() as s:
        t = Temple(temple_code="RACE-T", name="Race temple")
        s.add(t)
        s.flush()
        s.add(AnnadhanamHall(hall_code="RACE-H", temple_id=t.id, name="Race hall"))
        s.flush()
        s.add(Seat(seat_id="S001", hall_id="RACE-H"))
        s.commit()

    barrier = threading.Barrier(8)
    outcomes: list[str] = []
    lock = threading.Lock()

    def operator():
        with factory() as s:
            barrier.wait()
            try:
                seat_status_service.change_status(s, "RACE-H", "S001",
                                                  SeatStatusChange(status="OCCUPIED"), "operator")
                result = "ok"
            except ConflictError:
                result = "conflict"
            with lock:
                outcomes.append(result)

    threads = [threading.Thread(target=operator) for _ in range(8)]
    try:
        for th in threads:
            th.start()
        for th in threads:
            th.join(20)
        assert sorted(outcomes) == ["conflict"] * 7 + ["ok"]
        with factory() as s:
            seat = s.scalar(select(Seat).where(Seat.hall_id == "RACE-H"))
            assert seat.status == "OCCUPIED" and seat.status_version == 2
            events = s.scalar(text("SELECT count(*) FROM seat_status_events WHERE hall_id = 'RACE-H'"))
            assert events == 1
    finally:
        with factory() as s:
            s.execute(text("DELETE FROM seats WHERE hall_id = 'RACE-H'"))
            s.execute(text("DELETE FROM annadhanam_halls WHERE hall_code = 'RACE-H'"))
            s.execute(text("DELETE FROM temples WHERE temple_code = 'RACE-T'"))
            s.commit()


def test_database_rejects_reservation_data_on_non_reserved_seat(session, hall_factory):
    from sqlalchemy.exc import IntegrityError

    from app.models import Seat

    hall_factory("DBH")
    session.add(Seat(seat_id="S1", hall_id="DBH", status="OCCUPIED", reservation_ref="X"))
    with pytest.raises(IntegrityError):
        session.flush()
