"""Database engine, session factory and connectivity check.

PostgreSQL only, via SQLAlchemy 2.x and psycopg 3.

The schema is owned by Alembic. Base.metadata.create_all() is never called here
and must not be used as the migration system.
"""

import logging
from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def build_engine(database_url: str | None = None, **overrides: Any) -> Engine:
    """Create a pooled SQLAlchemy engine for PostgreSQL.

    QueuePool is SQLAlchemy's default for PostgreSQL; the sizing below is explicit
    so the behaviour under load is intentional rather than inherited.
    """
    url = database_url or settings.database_url
    options: dict[str, Any] = {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": settings.db_pool_recycle,
        "pool_pre_ping": True,  # drop dead connections instead of erroring mid-request
        "echo": settings.db_echo,
        "future": True,
        "connect_args": {
            "connect_timeout": settings.db_connect_timeout,
            "application_name": "temple_annadhanam_ai",
        },
    }
    options.update(overrides)

    # QueuePool-only options are invalid for pools such as NullPool, which Alembic
    # uses for migrations. Drop them when a different pool class is requested.
    if "poolclass" in options:
        poolclass = options["poolclass"]
        if getattr(poolclass, "__name__", "") != "QueuePool":
            for key in ("pool_size", "max_overflow", "pool_timeout"):
                options.pop(key, None)

    return create_engine(url, **options)


# Module-level engine shared by the application.
# Creating an engine does not open a connection, so import stays safe when
# PostgreSQL is down; the failure surfaces on first use instead.
engine: Engine = build_engine()

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session, rolled back on error and always closed."""
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_connection(target_engine: Engine | None = None) -> dict[str, Any]:
    """Run a lightweight query and report connectivity.

    Never raises: returns a status dict so a database outage degrades the health
    endpoint rather than taking down the application.
    """
    active = target_engine or engine
    try:
        with active.connect() as connection:
            connection.execute(text("SELECT 1"))
            server_version = connection.execute(text("SHOW server_version")).scalar_one()
            database_name = connection.execute(text("SELECT current_database()")).scalar_one()
        return {
            "status": "ok",
            "database": "postgresql",
            "database_name": database_name,
            "server_version": server_version,
            "driver": active.dialect.driver,
            "pool_size": active.pool.size(),
            "connections_in_use": active.pool.checkedout(),
        }
    except SQLAlchemyError as exc:
        logger.warning("Database connectivity check failed: %s", exc)
        return {
            "status": "error",
            "database": "postgresql",
            "detail": _root_cause(exc),
        }


def _root_cause(exc: BaseException) -> str:
    """Innermost driver message, trimmed. Credentials are not part of these messages."""
    cause: BaseException = exc
    while cause.__cause__ is not None:
        cause = cause.__cause__
    return " ".join(str(cause).split())[:300]
