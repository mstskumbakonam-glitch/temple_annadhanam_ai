"""Crowd analytics persistence: alerts and per-minute count history.

Both tables are anonymous: they hold counts, zone ids and thresholds, never a
person, a face or an image.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, bigint_pk
from app.models.enums import AlertSeverity, AlertType, values

if TYPE_CHECKING:
    from app.models.camera import Camera


class CrowdAlert(Base, TimestampMixin):
    """One debounced alert, from the moment it was raised until it cleared."""

    __tablename__ = "crowd_alerts"

    id: Mapped[int] = bigint_pk()
    alert_uid: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    camera_id: Mapped[int] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zone_id: Mapped[str | None] = mapped_column(String(32))
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    peak_value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str | None] = mapped_column(Text)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB(none_as_null=True)
    )

    camera: Mapped[Camera] = relationship()

    __table_args__ = (
        CheckConstraint(
            f"alert_type IN ({', '.join(repr(v) for v in values(AlertType))})",
            name="alert_type_valid",
        ),
        CheckConstraint(
            f"severity IN ({', '.join(repr(v) for v in values(AlertSeverity))})",
            name="severity_valid",
        ),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ended_after_started"),
        Index("ix_crowd_alerts_started_at", "started_at"),
        # "Which alerts are still open?" is the dashboard's hottest query.
        Index("ix_crowd_alerts_open", "camera_id", postgresql_where=text("ended_at IS NULL")),
    )


class CrowdCountSnapshot(Base):
    """People counted in one camera (zone_id='*') or one zone, aggregated per minute."""

    __tablename__ = "crowd_count_snapshots"

    id: Mapped[int] = bigint_pk()
    camera_id: Mapped[int] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False
    )
    zone_id: Mapped[str] = mapped_column(String(32), nullable=False)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    samples: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_count: Mapped[float] = mapped_column(Float, nullable=False)
    max_count: Mapped[int] = mapped_column(Integer, nullable=False)
    entries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    exits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("camera_id", "zone_id", "bucket_start"),
        CheckConstraint("samples > 0", name="samples_positive"),
        CheckConstraint("avg_count >= 0 AND max_count >= 0", name="counts_non_negative"),
        CheckConstraint("entries >= 0 AND exits >= 0", name="crossings_non_negative"),
        Index("ix_crowd_count_snapshots_bucket_start", "bucket_start"),
    )
