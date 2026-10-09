"""Alembic configuration checks."""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


@pytest.fixture(scope="module")
def alembic_config() -> Config:
    return Config(str(ALEMBIC_INI))


def test_alembic_ini_exists():
    assert ALEMBIC_INI.is_file()


def test_alembic_ini_contains_no_credentials():
    """Credentials belong in .env, never in a tracked config file.

    Only active (non-comment) lines are inspected, so explanatory comments
    mentioning passwords do not trip the check.
    """
    active_lines = [
        line.strip()
        for line in ALEMBIC_INI.read_text().splitlines()
        if line.strip() and not line.strip().startswith(("#", ";"))
    ]
    assert not any(line.startswith("sqlalchemy.url") for line in active_lines), (
        "alembic.ini must not define sqlalchemy.url; env.py reads it from .env"
    )
    assert not any("://" in line for line in active_lines), (
        "alembic.ini must not contain a database URL"
    )


def test_script_directory_is_valid(alembic_config):
    script = ScriptDirectory.from_config(alembic_config)
    assert Path(script.dir).is_dir()
    assert (Path(script.dir) / "env.py").is_file()
    assert (Path(script.dir) / "versions").is_dir()


def test_env_py_reads_url_from_settings():
    env_py = (BACKEND_DIR / "alembic" / "env.py").read_text()
    assert "get_settings" in env_py
    assert "target_metadata = Base.metadata" in env_py


def test_revision_history_is_linear(alembic_config):
    """No branch points, so 'upgrade head' is unambiguous."""
    script = ScriptDirectory.from_config(alembic_config)
    heads = script.get_heads()
    assert len(heads) <= 1, f"Multiple Alembic heads found: {heads}"
