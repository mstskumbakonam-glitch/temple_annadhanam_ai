"""Append-only audit tables for visitor and camera events."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, bigint_pk
from app.models.enums import CameraEventType, VisitorEventType, values

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.visitor import Visitor


class VisitorEvent(Base, CreatedAtMixin):
    """A single visitor lifecycle event. Written once, never updated."""

    __tablename__ = "visitor_events"

    id: Mapped[int] = bigint_pk()

    visitor_id: Mapped[int | None] = mapped_column(
        ForeignKey("visitors.id", ondelete="RESTRICT"), index=True
    )
    camera_id: Mapped[int | None] = mapped_column(
        ForeignKey("cameras.id", ondelete="SET NULL"), index=True
    )

    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    tracking_id: Mapped[int | None] = mapped_column(BigInteger)

    # 'metadata' is reserved by SQLAlchemy's Declarative API, so the attribute is
    # event_metadata while the column keeps the requested name.
    # none_as_null=True keeps an absent payload as SQL NULL, not JSON 'null'.
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB(none_as_null=True)
    )

    visitor: Mapped[Visitor | None] = relationship(back_populates="events")
    camera: Mapped[Camera | None] = relationship(back_populates="visitor_events")

    __table_args__ = (
        CheckConstraint(
            f"event_type IN ({', '.join(repr(v) for v in values(VisitorEventType))})",
            name="event_type_valid",
        ),
        Index("ix_visitor_events_event_time", "event_time"),
        Index("ix_visitor_events_event_type", "event_type"),
        # Timeline for one visitor, newest first.
        Index("ix_visitor_events_visitor_id_event_time", "visitor_id", "event_time"),
    )

    def __repr__(self) -> str:
        return f"<VisitorEvent {self.event_type} visitor_id={self.visitor_id}>"


class CameraEvent(Base, CreatedAtMixin):
    """A camera health/status transition. Written once, never updated."""

    __tablename__ = "camera_events"

    id: Mapped[int] = bigint_pk()

    camera_id: Mapped[int] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )

    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    fps: Mapped[float | None] = mapped_column()
    message: Mapped[str | None] = mapped_column(Text)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB(none_as_null=True)
    )

    camera: Mapped[Camera] = relationship(back_populates="camera_events")

    __table_args__ = (
        CheckConstraint(
            f"event_type IN ({', '.join(repr(v) for v in values(CameraEventType))})",
            name="event_type_valid",
        ),
        CheckConstraint("fps IS NULL OR fps >= 0", name="fps_non_negative"),
        Index("ix_camera_events_event_time", "event_time"),
        Index("ix_camera_events_event_type", "event_type"),
        Index("ix_camera_events_camera_id_event_time", "camera_id", "event_time"),
    )

    def __repr__(self) -> str:
        return f"<CameraEvent {self.event_type} camera_id={self.camera_id}>"
