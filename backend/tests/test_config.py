"""Environment validation: PostgreSQL required, SQLite rejected."""

import pytest

from app.config import Settings, mask_url_password, validate_postgres_url

VALID = "postgresql+psycopg://postgres:secret@localhost:5432/temple_annadhanam"


def test_valid_postgres_url_accepted():
    assert validate_postgres_url(VALID, "DATABASE_URL") == VALID


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///temple.db",
        "sqlite+aiosqlite:///temple.db",
        "postgresql://postgres:secret@localhost:5432/db",  # psycopg2 driver
        "postgresql+asyncpg://postgres:secret@localhost:5432/db",
        "mysql://root@localhost/db",
        "",
    ],
)
def test_non_psycopg_postgres_urls_rejected(url):
    with pytest.raises(ValueError):
        validate_postgres_url(url, "DATABASE_URL")


def test_sqlite_error_message_is_explicit():
    with pytest.raises(ValueError, match="PostgreSQL only"):
        validate_postgres_url("sqlite:///temple.db", "DATABASE_URL")


def test_database_url_is_required():
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_settings_reject_sqlite_database_url():
    with pytest.raises(ValueError):
        Settings(_env_file=None, database_url="sqlite:///temple.db")


def test_settings_reject_sqlite_test_database_url():
    with pytest.raises(ValueError):
        Settings(_env_file=None, database_url=VALID, test_database_url="sqlite:///t.db")


def test_password_is_masked_for_logging():
    masked = Settings(_env_file=None, database_url=VALID).safe_database_url
    assert "secret" not in masked
    assert "***" in masked
    assert "postgres" in masked


def test_mask_handles_url_without_credentials():
    url = "postgresql+psycopg://localhost:5432/db"
    assert mask_url_password(url) == url


def test_seat_and_face_defaults_match_specification():
    s = Settings(_env_file=None, database_url=VALID)
    assert s.face_match_threshold == 0.45
    assert s.seat_occupancy_confirm_frames == 5
    assert s.seat_empty_confirm_frames == 10
    assert s.seat_occupancy_timeout == 3
