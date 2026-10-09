"""Declarative base and metadata conventions."""

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase

from app.models import Base
from app.models.base import NAMING_CONVENTION, TimestampMixin
from app.utils.time import to_utc, utc_now


def test_base_is_declarative():
    assert issubclass(Base, DeclarativeBase)


def test_metadata_uses_naming_convention():
    assert Base.metadata.naming_convention == NAMING_CONVENTION
    assert Base.metadata.naming_convention["pk"] == "pk_%(table_name)s"


def test_metadata_contains_all_core_tables():
    """Importing app.models must register every table for Alembic autogenerate."""
    expected = {
        "cameras",
        "visitors",
        "staff",
        "staff_attendance",
        "seats",
        "seat_occupancy",
        "visitor_events",
        "camera_events",
    }
    assert expected <= set(Base.metadata.tables)


def test_timestamp_mixin_columns_are_timezone_aware():
    class Sample(Base, TimestampMixin):
        __tablename__ = "sample_timestamp_check"
        from sqlalchemy.orm import Mapped, mapped_column

        id: Mapped[int] = mapped_column(primary_key=True)

    created = Sample.__table__.c.created_at
    updated = Sample.__table__.c.updated_at
    assert isinstance(created.type, DateTime) and created.type.timezone is True
    assert isinstance(updated.type, DateTime) and updated.type.timezone is True
    assert created.server_default is not None

    Base.metadata.remove(Sample.__table__)  # keep metadata clean for other tests


def test_utc_now_is_timezone_aware():
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 0


def test_to_utc_treats_naive_as_utc():
    from datetime import datetime

    assert to_utc(datetime(2026, 1, 1, 12, 0)).utcoffset().total_seconds() == 0
