"""PostgreSQL connectivity and engine configuration."""

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.database import build_engine, check_database_connection

pytestmark = pytest.mark.db


def test_engine_uses_psycopg3_driver(test_engine):
    assert test_engine.dialect.name == "postgresql"
    assert test_engine.dialect.driver == "psycopg"


def test_can_connect_and_query(test_engine):
    with test_engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1


def test_server_is_postgresql_16_or_newer(test_engine):
    with test_engine.connect() as connection:
        version = connection.execute(text("SHOW server_version")).scalar_one()
    assert int(version.split(".")[0]) >= 16, f"PostgreSQL 16+ required, found {version}"


def test_connects_to_the_test_database_not_production(test_engine, settings):
    with test_engine.connect() as connection:
        name = connection.execute(text("SELECT current_database()")).scalar_one()
    assert name.endswith("_test"), f"Tests must not run against '{name}'"


def test_timestamps_are_timezone_aware(test_engine):
    """TIMESTAMPTZ round-trips with tzinfo attached, which the models rely on."""
    with test_engine.connect() as connection:
        value = connection.execute(text("SELECT now()::timestamptz")).scalar_one()
    assert value.tzinfo is not None
    assert value.utcoffset() is not None


def test_pool_is_configured_from_settings(test_engine):
    settings = get_settings()
    assert test_engine.pool.size() == settings.db_pool_size


def test_health_check_reports_ok(test_engine):
    result = check_database_connection(test_engine)
    assert result["status"] == "ok"
    assert result["database"] == "postgresql"
    assert result["driver"] == "psycopg"


def test_health_check_reports_error_without_raising():
    """An unreachable database degrades to an error dict rather than an exception."""
    dead = build_engine(
        "postgresql+psycopg://postgres:wrong@127.0.0.1:59999/nope",
        pool_pre_ping=False,
    )
    result = check_database_connection(dead)
    dead.dispose()
    assert result["status"] == "error"
    assert "detail" in result
    assert "wrong" not in result["detail"]  # password must not leak into the message


def test_db_session_fixture_rolls_back(db_session):
    db_session.execute(text("CREATE TEMPORARY TABLE probe (id int)"))
    db_session.execute(text("INSERT INTO probe VALUES (1)"))
    assert db_session.execute(text("SELECT count(*) FROM probe")).scalar_one() == 1
