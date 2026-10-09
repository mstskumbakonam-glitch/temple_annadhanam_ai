"""Temples and halls: CRUD, search, filters, relationships, deletion rules."""

import pytest

pytestmark = pytest.mark.db


def temple(client, code="KMB-01", **kw):
    body = {"temple_code": code, "name": kw.pop("name", f"Temple {code}"),
            "district": kw.pop("district", "Thanjavur"), **kw}
    r = client.post("/api/temples", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def hall(client, code, temple_code="KMB-01", **kw):
    r = client.post("/api/halls", json={"hall_code": code, "temple_code": temple_code,
                                         "name": kw.pop("name", f"Hall {code}"), **kw})
    assert r.status_code == 201, r.text
    return r.json()


def seats(client, hall_code, rows=2, columns=5):
    r = client.post(f"/api/halls/{hall_code}/seats/generate", json={"rows": rows, "columns": columns})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ temples
def test_create_and_read_temple(api_client):
    body = temple(api_client, "kmb-01", address="Car Street", contact_phone="+91 435 240 0000")
    assert body["temple_code"] == "KMB-01"            # codes are normalised to upper case
    assert body["hall_count"] == 0 and body["seats"]["capacity"] == 0
    assert body["is_demo"] is False
    got = api_client.get("/api/temples/KMB-01").json()
    assert got["address"] == "Car Street"


def test_duplicate_temple_code_conflicts(api_client):
    temple(api_client)
    r = api_client.post("/api/temples", json={"temple_code": "KMB-01", "name": "Another"})
    assert r.status_code == 409


@pytest.mark.parametrize("bad", [{"name": ""}, {"contact_phone": "call me"}, {"temple_code": "has space"}])
def test_temple_validation(api_client, bad):
    body = {"temple_code": "OK-1", "name": "Ok", **bad}
    assert api_client.post("/api/temples", json=body).status_code == 422


def test_search_filter_and_pagination(api_client):
    temple(api_client, "T1", name="Sri Meenakshi", district="Madurai")
    temple(api_client, "T2", name="Sri Ranganathaswamy", district="Tiruchirappalli")
    temple(api_client, "T3", name="Kumbeswarar", district="Thanjavur", active=False)

    names = [t["name"] for t in api_client.get("/api/temples", params={"search": "sri"}).json()["items"]]
    assert names == ["Sri Meenakshi", "Sri Ranganathaswamy"]
    by_district = api_client.get("/api/temples", params={"district": "madurai"}).json()
    assert [t["temple_code"] for t in by_district["items"]] == ["T1"]
    inactive = api_client.get("/api/temples", params={"active": False}).json()
    assert [t["temple_code"] for t in inactive["items"]] == ["T3"]
    page = api_client.get("/api/temples", params={"page_size": 2, "page": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 1
    assert api_client.get("/api/temples/districts").json() == ["Madurai", "Thanjavur", "Tiruchirappalli"]


def test_edit_temple(api_client):
    temple(api_client)
    r = api_client.put("/api/temples/KMB-01", json={"name": "Renamed", "active": False})
    assert r.status_code == 200 and r.json()["name"] == "Renamed" and r.json()["active"] is False
    assert api_client.put("/api/temples/NOPE", json={"name": "x"}).status_code == 404


# -------------------------------------------------------------------- halls
def test_hall_belongs_to_temple_and_counts_roll_up(api_client):
    temple(api_client)
    hall(api_client, "KMB-01-H1", building="North block", floor="Ground")
    hall(api_client, "KMB-01-H2")
    seats(api_client, "KMB-01-H1", 2, 5)        # 10
    seats(api_client, "KMB-01-H2", 3, 4)        # 12

    t = api_client.get("/api/temples/KMB-01").json()
    assert t["hall_count"] == 2 and t["seats"]["capacity"] == 22 and t["seats"]["available"] == 22
    h = api_client.get("/api/halls/KMB-01-H1").json()
    assert h["temple_code"] == "KMB-01" and h["seats"]["installed"] == 10 and h["building"] == "North block"
    listed = api_client.get("/api/halls", params={"temple_code": "KMB-01"}).json()
    assert [x["hall_code"] for x in listed["items"]] == ["KMB-01-H1", "KMB-01-H2"]


def test_inactive_hall_is_excluded_from_temple_totals_but_keeps_its_own(api_client):
    temple(api_client)
    hall(api_client, "H-A")
    hall(api_client, "H-B", active=False)
    seats(api_client, "H-A", 1, 4)
    seats(api_client, "H-B", 1, 6)
    assert api_client.get("/api/temples/KMB-01").json()["seats"]["capacity"] == 4
    assert api_client.get("/api/halls/H-B").json()["seats"]["capacity"] == 6


def test_hall_needs_existing_temple_and_unique_code(api_client):
    r = api_client.post("/api/halls", json={"hall_code": "X1", "temple_code": "NOPE", "name": "X"})
    assert r.status_code == 404
    temple(api_client)
    hall(api_client, "X1")
    r = api_client.post("/api/halls", json={"hall_code": "X1", "temple_code": "KMB-01", "name": "Y"})
    assert r.status_code == 409


def test_move_hall_to_another_temple(api_client):
    temple(api_client, "T1")
    temple(api_client, "T2")
    hall(api_client, "H1", "T1")
    r = api_client.put("/api/halls/H1", json={"temple_code": "T2"})
    assert r.status_code == 200 and r.json()["temple_code"] == "T2"
    assert api_client.get("/api/temples/T1").json()["hall_count"] == 0


def test_search_halls(api_client):
    temple(api_client)
    hall(api_client, "H1", name="Annadhanam Main Hall")
    hall(api_client, "H2", name="Prasadam Counter")
    r = api_client.get("/api/halls", params={"search": "main"}).json()
    assert [h["hall_code"] for h in r["items"]] == ["H1"]


def test_deletion_rules(api_client):
    temple(api_client)
    hall(api_client, "H1")
    hall(api_client, "H2")
    seats(api_client, "H1", 1, 2)
    assert api_client.delete("/api/temples/KMB-01").status_code == 409       # still has halls
    assert api_client.delete("/api/halls/H1").status_code == 409             # has seats
    assert api_client.delete("/api/halls/H2").status_code == 204
    assert api_client.get("/api/halls/H2").status_code == 404


def test_generate_layout_is_idempotent(api_client):
    temple(api_client)
    hall(api_client, "H1")
    first = seats(api_client, "H1", 2, 3)
    assert first == {"created": 6, "skipped_existing": 0, "hall_id": "H1"}
    again = seats(api_client, "H1", 3, 3)
    assert again["created"] == 3 and again["skipped_existing"] == 6
    listed = api_client.get("/api/halls/H1/seats").json()
    assert [s["seat_id"] for s in listed][:3] == ["S001", "S002", "S003"]
    assert listed[0]["row_number"] == 1 and listed[0]["status"] == "AVAILABLE"
    r = api_client.post("/api/halls/H1/seats/generate", json={"rows": 100, "columns": 100})
    assert r.status_code == 422                                              # > 2000 seats
