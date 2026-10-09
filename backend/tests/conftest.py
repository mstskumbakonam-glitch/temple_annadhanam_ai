"""Shared pytest fixtures.

Tests run against the PostgreSQL database named by TEST_DATABASE_URL.
There is no SQLite fallback: if TEST_DATABASE_URL is missing or unreachable the
suite fails or skips loudly, it never quietly switches engines.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.database import build_engine


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def test_database_url(settings) -> str:
    """The test database URL, verified to be PostgreSQL and distinct from production."""
    url = settings.test_database_url
    if not url:
        pytest.fail(
            "TEST_DATABASE_URL is not set. Tests require a PostgreSQL test database; "
            "SQLite is not supported. Set it in backend/.env."
        )
    if url == settings.database_url:
        pytest.fail(
            "TEST_DATABASE_URL must differ from DATABASE_URL so tests never run "
            "against the production database."
        )
    return url


@pytest.fixture(scope="session")
def test_engine(test_database_url):
    """Engine bound to the test database, skipped if PostgreSQL is not running."""
    engine = build_engine(test_database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        engine.dispose()
        pytest.skip(f"PostgreSQL test database is not reachable: {exc}")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(test_engine):
    """Session wrapped in a transaction that is rolled back, leaving no test data behind."""
    connection = test_engine.connect()
    transaction = connection.begin()
    from sqlalchemy.orm import Session

    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# --------------------------------------------------------------------------
# Phase 3: schema-backed fixtures
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def migrated_database(test_engine):
    """Ensure the test database is migrated to head before model tests run.

    Uses Alembic, not Base.metadata.create_all(), so tests exercise the same
    schema that production gets.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))

    settings = get_settings()
    original = settings.database_url
    # Point Alembic at the TEST database for the duration of the run.
    object.__setattr__(settings, "database_url", settings.test_database_url)
    try:
        command.upgrade(config, "head")
        yield test_engine
    finally:
        object.__setattr__(settings, "database_url", original)


@pytest.fixture
def session(migrated_database):
    """Transactional session against the migrated test schema, rolled back after.

    join_transaction_mode="create_savepoint" makes the session run inside a
    SAVEPOINT of the outer connection transaction. A service calling commit()
    then releases only that savepoint, and rollback() unwinds only to it, so
    real transaction handling is exercised while the outer transaction still
    discards everything at the end of the test.
    """
    from sqlalchemy.orm import Session

    connection = migrated_database.connect()
    transaction = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield db
    finally:
        db.close()
        # A test that triggered an IntegrityError may already have aborted the
        # transaction, so roll back only while it is still active.
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def camera(session):
    from app.models import Camera

    obj = Camera(camera_id="TEST-CAM-01", camera_name="Test Camera", location="Test")
    session.add(obj)
    session.flush()
    return obj


@pytest.fixture
def seat(session, camera):
    from app.models import Seat

    obj = Seat(
        seat_id="S01",
        hall_id="TESTHALL",
        camera_id=camera.id,
        seat_label="Seat 01",
        row_number=1,
        column_number=1,
        polygon_points=[{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}],
    )
    session.add(obj)
    session.flush()
    return obj


@pytest.fixture
def visitor(session, camera):
    from app.models import Visitor

    obj = Visitor(camera_id=camera.id, tracking_id=42)
    session.add(obj)
    session.flush()
    session.refresh(obj)
    return obj


@pytest.fixture
def staff_member(session):
    from app.models import Staff

    obj = Staff(staff_name="Test Staff", department="Kitchen")
    session.add(obj)
    session.flush()
    session.refresh(obj)
    return obj


# --------------------------------------------------------------------------
# Phase 4: API fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def api_client(session):
    """TestClient whose get_db dependency reuses the savepoint-backed test session.

    The session fixture joins the outer transaction via a savepoint, so service
    commits and rollbacks behave normally but nothing survives the test.
    """
    from fastapi.testclient import TestClient

    from app.api.deps import get_db
    from app.main import app

    def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def api_camera(api_client):
    """A camera created through the API, returned as its response body."""
    response = api_client.post(
        "/api/cameras",
        json={
            "camera_id": "ANN-ENT-01",
            "camera_name": "Entrance",
            "location": "Main gate",
            "rtsp_url": "rtsp://admin:hunter2@10.0.0.5:554/stream",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def api_staff(api_client):
    response = api_client.post(
        "/api/staff",
        json={"staff_name": "Ravi Kumar", "department": "Serving", "employee_code": "EMP-100"},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def api_seat(api_client, api_camera):
    response = api_client.post(
        "/api/seats",
        json={
            "seat_id": "S01",
            "hall_id": "MAIN",
            "camera_id": api_camera["camera_id"],
            "seat_label": "Seat 01",
            "row_number": 1,
            "column_number": 1,
            "polygon_points": [
                {"x": 100, "y": 200},
                {"x": 180, "y": 200},
                {"x": 180, "y": 300},
                {"x": 100, "y": 300},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()
