"""Staff assignment, attendance register, dashboard totals, reports and permissions."""

import csv
import io
from datetime import timedelta

import pytest

from app.config import get_settings
from app.services.session_service import local_today

pytestmark = pytest.mark.db

TZ = lambda: get_settings().site_timezone  # noqa: E731


@pytest.fixture
def site(api_client):
    """Two temples (one inactive), three halls (one inactive), seats and staff."""
    c = api_client
    c.post("/api/temples", json={"temple_code": "T1", "name": "Temple One", "district": "Thanjavur"})
    c.post("/api/temples", json={"temple_code": "T2", "name": "Temple Two", "active": False})
    c.post("/api/halls", json={"hall_code": "T1-H1", "temple_code": "T1", "name": "Main hall"})
    c.post("/api/halls", json={"hall_code": "T1-H2", "temple_code": "T1", "name": "Old hall", "active": False})
    c.post("/api/halls", json={"hall_code": "T2-H1", "temple_code": "T2", "name": "Hall"})
    c.post("/api/halls/T1-H1/seats/generate", json={"rows": 2, "columns": 10})    # 20
    c.post("/api/halls/T1-H2/seats/generate", json={"rows": 1, "columns": 5})     # inactive hall
    c.post("/api/halls/T2-H1/seats/generate", json={"rows": 1, "columns": 7})     # inactive temple
    for seat in ("S001", "S002", "S003", "S004"):
        c.post(f"/api/halls/T1-H1/seats/{seat}/status", json={"status": "OCCUPIED"})
    c.post("/api/halls/T1-H1/seats/S005/status", json={"status": "RESERVED"})
    c.post("/api/halls/T1-H1/seats/S006/status", json={"status": "OUT_OF_SERVICE"})
    staff = {}
    for name, shift in (("Ravi", "MORNING"), ("Meena", "EVENING"), ("Kumar", "MORNING")):
        r = c.post("/api/staff", json={"staff_name": name, "temple_code": "T1", "hall_code": "T1-H1",
                                       "shift": shift, "designation": "Cook", "phone": "+91 98400 00000"})
        assert r.status_code == 201, r.text
        staff[name] = r.json()["staff_code"]
    c.post("/api/staff", json={"staff_name": "Retired", "active": False})
    return staff


# --------------------------------------------------------------------- staff
def test_staff_assignment_and_filters(api_client, site):
    r = api_client.get("/api/staff", params={"temple_code": "T1", "shift": "MORNING"}).json()
    assert sorted(s["staff_name"] for s in r["items"]) == ["Kumar", "Ravi"]
    r = api_client.get("/api/staff", params={"search": "mee"}).json()
    assert [s["hall_code"] for s in r["items"]] == ["T1-H1"]


def test_hall_must_belong_to_the_assigned_temple(api_client, site):
    r = api_client.post("/api/staff", json={"staff_name": "X", "temple_code": "T2", "hall_code": "T1-H1"})
    assert r.status_code == 422
    r = api_client.post("/api/staff", json={"staff_name": "Y", "hall_code": "T2-H1"})
    assert r.status_code == 201 and r.json()["temple_code"] == "T2"      # temple follows the hall


def test_invalid_shift_or_phone(api_client):
    assert api_client.post("/api/staff", json={"staff_name": "Z", "shift": "LATE"}).status_code == 422
    assert api_client.post("/api/staff", json={"staff_name": "Z", "phone": "abc"}).status_code == 422


# ---------------------------------------------------------------- attendance
def test_attendance_register_never_counts_unmarked_as_absent(api_client, site):
    reg = api_client.get("/api/attendance").json()
    assert len(reg["rows"]) == 3 and all(r["status"] is None for r in reg["rows"])
    assert reg["summary"]["not_marked"] == 3 and reg["summary"]["absent"] == 0
    assert reg["summary"]["in_use"] is False

    today = local_today(TZ()).isoformat()
    r = api_client.put("/api/attendance", json={"entries": [
        {"staff_code": site["Ravi"], "attendance_date": today, "status": "PRESENT"},
        {"staff_code": site["Meena"], "attendance_date": today, "status": "LEAVE"},
    ]})
    assert r.status_code == 200 and r.json() == {"saved": 2}
    s = api_client.get("/api/attendance").json()["summary"]
    assert (s["present"], s["on_leave"], s["absent"], s["not_marked"], s["in_use"]) == (1, 1, 0, 1, True)

    # re-marking updates the same row instead of creating a duplicate
    api_client.put("/api/attendance", json={"entries": [
        {"staff_code": site["Ravi"], "attendance_date": today, "status": "HALF_DAY"}]})
    s = api_client.get("/api/attendance").json()["summary"]
    assert (s["present"], s["half_day"]) == (0, 1)
    staff = {x["staff_name"]: x for x in api_client.get("/api/staff").json()["items"]}
    assert staff["Ravi"]["today_attendance"] == "HALF_DAY" and staff["Kumar"]["today_attendance"] is None


def test_attendance_rejects_future_dates_and_unknown_staff(api_client, site):
    tomorrow = (local_today(TZ()) + timedelta(days=1)).isoformat()
    r = api_client.put("/api/attendance", json={"entries": [
        {"staff_code": site["Ravi"], "attendance_date": tomorrow, "status": "PRESENT"}]})
    assert r.status_code == 422
    r = api_client.put("/api/attendance", json={"entries": [
        {"staff_code": "STAFF-999", "attendance_date": "2026-01-01", "status": "PRESENT"}]})
    assert r.status_code == 404


# ----------------------------------------------------------------- dashboard
def test_dashboard_overview_matches_the_fixture(api_client, site):
    today = local_today(TZ())
    api_client.post("/api/sessions", json={"hall_code": "T1-H1", "name": "Lunch", "meal_type": "LUNCH",
                                           "session_date": today.isoformat(), "start_time": "00:00",
                                           "end_time": "00:30"})
    api_client.post("/api/sessions", json={"hall_code": "T1-H1", "name": "Future", "meal_type": "DINNER",
                                           "session_date": "2031-01-01", "start_time": "19:00",
                                           "end_time": "20:00"})
    api_client.put("/api/attendance", json={"entries": [
        {"staff_code": site["Ravi"], "attendance_date": today.isoformat(), "status": "PRESENT"},
        {"staff_code": site["Kumar"], "attendance_date": today.isoformat(), "status": "ABSENT"}]})

    o = api_client.get("/api/dashboard/overview").json()
    assert (o["temples_total"], o["temples_active"]) == (2, 1)
    assert (o["halls_total"], o["halls_active"]) == (3, 1)
    # Only the active hall of the active temple counts: 20 seats, 1 out of service.
    assert o["seats"] == {"installed": 20, "capacity": 19, "available": 14, "occupied": 4, "reserved": 1,
                          "out_of_service": 1, "occupancy_percentage": round(4 / 19 * 100, 2)}
    assert o["staff_total_active"] == 3
    a = o["attendance"]
    assert (a["present"], a["absent"], a["not_marked"]) == (1, 1, 1)
    assert o["sessions"]["today_total"] == 1 and o["sessions"]["upcoming"] == 1
    assert [s["name"] for s in o["todays_sessions"]] == ["Lunch"]
    assert o["active_alerts"] == 0 and o["demo_records_present"] is False

    scoped = api_client.get("/api/dashboard/overview", params={"temple_code": "T2"}).json()
    assert scoped["temples_total"] == 1 and scoped["seats"]["capacity"] == 0   # inactive temple
    assert scoped["active_alerts"] is None


def test_dashboard_on_empty_database(api_client):
    o = api_client.get("/api/dashboard/overview").json()
    assert o["temples_total"] == 0 and o["seats"]["capacity"] == 0
    assert o["seats"]["occupancy_percentage"] == 0.0
    assert o["attendance"]["in_use"] is False and o["staff_total_active"] == 0


# ------------------------------------------------------------------- reports
def test_reports_json_and_csv(api_client, site):
    cap = api_client.get("/api/reports/temple-capacity").json()
    rows = {(r["temple_code"], r["hall_code"]): r for r in cap["rows"]}
    assert rows[("T1", "T1-H1")]["seat_capacity"] == 19
    occ = api_client.get("/api/reports/hall-occupancy", params={"temple_code": "T1"}).json()
    assert {r["hall_code"]: r["occupied"] for r in occ["rows"]} == {"T1-H1": 4, "T1-H2": 0}

    r = api_client.get("/api/reports/hall-occupancy", params={"format": "csv"})
    assert r.headers["content-type"].startswith("text/csv")
    parsed = list(csv.DictReader(io.StringIO(r.text)))
    assert len(parsed) == 3 and "occupancy_percentage" in parsed[0]

    hist = api_client.get("/api/reports/seat-occupancy").json()
    assert hist["rows"][0]["hall_code"] == "T1-H1" and hist["rows"][0]["occupations_started"] == 4

    att = api_client.get("/api/reports/staff-attendance").json()
    assert all(r["not_marked"] == r["days_in_range"] for r in att["rows"])


def test_sessions_report_expected_vs_actual(api_client, site):
    past = (local_today(TZ()) - timedelta(days=1)).isoformat()
    s = api_client.post("/api/sessions", json={"hall_code": "T1-H1", "name": "Lunch", "meal_type": "LUNCH",
                                               "session_date": past, "start_time": "11:00", "end_time": "13:00",
                                               "expected_devotees": 100}).json()
    api_client.put(f"/api/sessions/{s['id']}", json={"status": "COMPLETED", "actual_devotees": 92})
    rep = api_client.get("/api/reports/sessions", params={"actual_recorded_only": True}).json()
    assert rep["rows"][0]["difference"] == -8


def test_csv_neutralises_spreadsheet_formulas(api_client):
    api_client.post("/api/temples", json={"temple_code": "EVIL", "name": "=HYPERLINK(\"x\")"})
    r = api_client.get("/api/reports/temple-capacity", params={"format": "csv"})
    assert "'=HYPERLINK" in r.text


def test_report_date_range_is_validated(api_client):
    r = api_client.get("/api/reports/sessions", params={"start_date": "2026-05-01", "end_date": "2026-04-01"})
    assert r.status_code == 422
    r = api_client.get("/api/reports/sessions", params={"start_date": "2020-01-01", "end_date": "2026-01-01"})
    assert r.status_code == 422


# --------------------------------------------------------------- permissions
VIEWER, OPERATOR, ADMIN = ("viewer-key-" + "v" * 20, "operator-key-" + "o" * 20, "admin-key-" + "a" * 20)


@pytest.fixture
def keys():
    s = get_settings()
    saved = s.api_viewer_keys, s.api_operator_keys, s.api_admin_keys
    object.__setattr__(s, "api_viewer_keys", VIEWER)
    object.__setattr__(s, "api_operator_keys", OPERATOR)
    object.__setattr__(s, "api_admin_keys", ADMIN)
    yield
    object.__setattr__(s, "api_viewer_keys", saved[0])
    object.__setattr__(s, "api_operator_keys", saved[1])
    object.__setattr__(s, "api_admin_keys", saved[2])


def h(key):
    return {"Authorization": f"Bearer {key}"}


def test_role_matrix(api_client, keys):
    c = api_client
    assert c.post("/api/temples", json={"temple_code": "P1", "name": "P"}, headers=h(OPERATOR)).status_code == 403
    assert c.post("/api/temples", json={"temple_code": "P1", "name": "P"}, headers=h(ADMIN)).status_code == 201
    assert c.post("/api/halls", json={"hall_code": "PH", "temple_code": "P1", "name": "PH"},
                  headers=h(OPERATOR)).status_code == 403
    c.post("/api/halls", json={"hall_code": "PH", "temple_code": "P1", "name": "PH"}, headers=h(ADMIN))
    assert c.post("/api/halls/PH/seats/generate", json={"rows": 1, "columns": 2},
                  headers=h(OPERATOR)).status_code == 403
    c.post("/api/halls/PH/seats/generate", json={"rows": 1, "columns": 2}, headers=h(ADMIN))

    seat = "/api/halls/PH/seats/S001/status"
    assert c.post(seat, json={"status": "OCCUPIED"}, headers=h(VIEWER)).status_code == 403
    r = c.post(seat, json={"status": "OCCUPIED"}, headers=h(OPERATOR))
    assert r.status_code == 200
    hist = c.get("/api/halls/PH/seats/history", headers=h(VIEWER)).json()
    assert hist["items"][0]["actor_role"] == "operator"

    session = {"hall_code": "PH", "name": "L", "meal_type": "LUNCH", "session_date": "2030-01-01",
               "start_time": "11:00", "end_time": "12:00"}
    assert c.post("/api/sessions", json=session, headers=h(VIEWER)).status_code == 403
    assert c.post("/api/sessions", json=session, headers=h(OPERATOR)).status_code == 201

    assert c.get("/api/attendance", headers=h(VIEWER)).status_code == 403
    assert c.get("/api/attendance", headers=h(OPERATOR)).status_code == 200
    assert c.get("/api/reports/staff-attendance", headers=h(VIEWER)).status_code == 403
    assert c.get("/api/reports/hall-occupancy", headers=h(VIEWER)).status_code == 200

    c.post("/api/staff", json={"staff_name": "Priya", "phone": "+91 90000 11111"}, headers=h(ADMIN))
    assert c.get("/api/staff", headers=h(VIEWER)).status_code == 403
    as_operator = c.get("/api/staff", headers=h(OPERATOR)).json()["items"][0]
    as_admin = c.get("/api/staff", headers=h(ADMIN)).json()["items"][0]
    assert as_operator["phone"] is None and as_admin["phone"] == "+91 90000 11111"
    assert c.post("/api/staff", json={"staff_name": "X"}, headers=h(OPERATOR)).status_code == 403

    assert c.get("/api/auth/me", headers=h(OPERATOR)).json()["role"] == "operator"
    assert c.get("/api/auth/me").status_code == 401
