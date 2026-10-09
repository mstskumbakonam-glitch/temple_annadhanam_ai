"""Cross-cutting privacy/security guarantees for Phase 5."""

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
APP = BACKEND / "app"

pytestmark = pytest.mark.db

GET_PATHS = [
    "/api/health", "/api/health/db", "/api/cameras", "/api/cameras/status",
    "/api/cameras/ANN-ENT-01", "/api/cameras/ANN-ENT-01/events", "/api/cameras/ANN-ENT-01/ai-status",
    "/api/ai/status", "/api/visitors", "/api/visitors/current", "/api/visitors/today",
    "/api/staff", "/api/staff/attendance", "/api/seats", "/api/seats/status",
    "/api/seats/history", "/api/dashboard/summary", "/api/halls/MAIN/occupancy",
]


def test_no_endpoint_ever_returns_a_face_embedding_or_a_password(api_client, api_camera, session):
    """Seed the sensitive data, then sweep every GET endpoint."""
    from sqlalchemy import select

    from app.models import Staff

    created = api_client.post("/api/staff", json={"staff_name": "Enrolled", "employee_code": "E-1"}).json()
    staff = session.scalar(select(Staff).where(Staff.staff_code == created["staff_code"]))
    staff.face_embedding = b"\x00SECRET-EMBEDDING-BYTES\x00"
    session.flush()
    api_client.post("/api/seats", json={"seat_id": "S01"})

    for path in GET_PATHS:
        response = api_client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"
        text = response.text
        assert "face_embedding" not in text, path
        assert "SECRET-EMBEDDING" not in text, path
        assert "hunter2" not in text, path                      # the camera's RTSP password
        assert "embedding" not in text.lower(), path


def test_openapi_schema_exposes_no_embedding_or_raw_rtsp_field(api_client):
    """Inspect actual schema *properties* (the description prose may mention the words)."""
    spec = api_client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]

    for name, schema in schemas.items():
        for prop in schema.get("properties", {}):
            assert "embedding" not in prop.lower(), f"{name}.{prop}"
            assert "password" not in prop.lower(), f"{name}.{prop}"

    # A raw rtsp_url may only be ACCEPTED (request bodies), never returned.
    for name, schema in schemas.items():
        if "rtsp_url" in schema.get("properties", {}):
            assert name in {"CameraCreate", "CameraUpdate"}, f"{name} returns raw rtsp_url"
    assert "rtsp_url_masked" in schemas["CameraRead"]["properties"]

    # visitors stay anonymous: no face-related route
    assert not [p for p in spec["paths"] if "face" in p.lower() or "embedding" in p.lower()]


def test_no_sqlite_anywhere_in_the_application():
    """PostgreSQL is the only database. The one place SQLite may be *named* is the
    validator that rejects it."""
    offenders = []
    for path in list((APP).rglob("*.py")) + list((BACKEND / "alembic").rglob("*.py")):
        text = path.read_text().lower()
        if "sqlite" in text and path.name != "config.py":
            offenders.append(str(path.relative_to(BACKEND)))
    assert offenders == []
    assert not re.search(r"^\s*(import|from)\s+sqlite3", (APP / "config.py").read_text(), re.M)

    requirements = (BACKEND / "requirements.txt").read_text().lower()
    assert "sqlite" not in requirements and "aiosqlite" not in requirements


def test_the_ai_code_never_stores_or_computes_face_data():
    """Phase 5 must not contain face recognition of any kind."""
    for path in list((APP / "ai").rglob("*.py")) + list((APP / "camera").rglob("*.py")):
        text = path.read_text().lower()
        code = "\n".join(l for l in text.splitlines() if not l.strip().startswith(("#", '"', "'")))
        for banned in ("face_recognition", "insightface", "facenet", "arcface", "dlib"):
            assert banned not in code, f"{path.name} references {banned}"


def test_no_rtsp_credentials_are_hard_coded_in_the_source():
    pattern = re.compile(r"rtsp://[^\s\"'/@:]+:[^\s\"'/@]+@")
    for path in APP.rglob("*.py"):
        assert not pattern.search(path.read_text()), f"credentials-like URL in {path.name}"


def test_env_example_holds_no_real_secrets_or_machine_paths():
    text = (BACKEND / ".env.example").read_text()
    assert "rtsp://" not in text
    assert not re.search(r"AI_MODEL_PATH=\s*(/|[A-Za-z]:\\)", text)   # no absolute path
    assert "AI_MODEL_PATH=" in text


def test_no_machine_specific_absolute_paths_in_ai_or_camera_code():
    for path in list((APP / "ai").rglob("*.py")) + list((APP / "camera").rglob("*.py")):
        for line in path.read_text().splitlines():
            if line.strip().startswith("#"):
                continue
            assert not re.search(r"[\"'](/home/|/Users/|C:\\\\|/mnt/|/tmp/)", line), f"{path.name}: {line}"


def test_ultralytics_runtime_installs_are_disabled_by_the_loader():
    text = (APP / "ai" / "model_loader.py").read_text()
    assert 'YOLO_AUTOINSTALL' in text and '"False"' in text
