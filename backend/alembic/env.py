"""Alembic migration environment.

The database URL comes from the application settings (backend/.env), never from
alembic.ini, so no password is stored in a tracked file.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from app.config import get_settings
from app.database import build_engine

# Import the models package so every model is registered on Base.metadata
# before autogenerate compares it against the live database.
from app.models import Base
import app.models  # noqa: F401  (Phase 3 models register themselves on import)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()

# Shown in Alembic's own logging; the password is masked.
config.set_main_option("sqlalchemy.url", settings.safe_database_url)

target_metadata = Base.metadata

# NOTE: compare_server_default is left as True (Alembic's built-in comparison).
# A custom callable cannot be used here: Alembic reads conn_col_default.arg.text
# for every column, which raises AttributeError on BIGINT IDENTITY primary keys.
# The sequence-backed defaults in app/models/sequences.py are therefore written
# in PostgreSQL's canonical form so the built-in comparison matches exactly and
# autogenerate does not report phantom changes.


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,          # detect column type changes
        compare_server_default=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Generate SQL without connecting (alembic upgrade --sql)."""
    _configure(
        url=settings.database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live PostgreSQL connection."""
    connectable = build_engine(settings.database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
