"""Health endpoints."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_returns_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_health_does_not_expose_secrets(client):
    assert "password" not in client.get("/api/health").text.lower()


@pytest.mark.db
def test_database_health_reports_postgresql(client, test_engine):
    response = client.get("/api/health/db")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "postgresql"
    assert body["driver"] == "psycopg"


@pytest.mark.db
def test_database_health_returns_503_when_unreachable(client, monkeypatch):
    """A database outage yields 503, not a 500 or a crash."""
    import app.api.health as health_module

    monkeypatch.setattr(
        health_module,
        "check_database_connection",
        lambda: {"status": "error", "database": "postgresql", "detail": "connection refused"},
    )
    response = client.get("/api/health/db")
    assert response.status_code == 503
    assert response.json()["status"] == "error"
