"""Visitor model. Visitors are anonymous: no face data is ever stored for them."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, bigint_pk
from app.models.enums import VisitorStatus, values
from app.models.sequences import visitor_code_default

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.events import VisitorEvent
    from app.models.occupancy import SeatOccupancy
    from app.models.seat import Seat


class Visitor(Base, TimestampMixin):
    """An anonymous visitor session.

    visitor_code (VIS-000001) is assigned by a PostgreSQL sequence default, so
    concurrent inserts can never collide.

    There is deliberately no face_embedding column: normal visitors are never
    identified by face.
    """

    __tablename__ = "visitors"

    id: Mapped[int] = bigint_pk()

    visitor_code: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        unique=True,
        server_default=visitor_code_default(),
    )

    # Tracker-assigned ID. Temporary and reused across sessions, so not unique.
    tracking_id: Mapped[int | None] = mapped_column(BigInteger)

    camera_id: Mapped[int | None] = mapped_column(
        ForeignKey("cameras.id", ondelete="SET NULL"), index=True
    )

    entry_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ACTIVE'")
    )

    # Denormalised pointer to the seat currently held, for fast dashboard reads.
    # seat_occupancy remains the authoritative history.
    current_seat_id: Mapped[int | None] = mapped_column(
        ForeignKey("seats.id", ondelete="SET NULL"), index=True
    )

    camera: Mapped[Camera | None] = relationship(back_populates="visitors")
    current_seat: Mapped[Seat | None] = relationship()
    events: Mapped[list[VisitorEvent]] = relationship(
        back_populates="visitor", order_by="VisitorEvent.event_time"
    )
    seat_occupancies: Mapped[list[SeatOccupancy]] = relationship(back_populates="visitor")

    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(v) for v in values(VisitorStatus))})",
            name="status_valid",
        ),
        CheckConstraint(
            "exit_time IS NULL OR entry_time IS NULL OR exit_time >= entry_time",
            name="exit_after_entry",
        ),
        Index("ix_visitors_status", "status"),
        Index("ix_visitors_entry_time", "entry_time"),
        Index("ix_visitors_last_seen", "last_seen"),
        # Supports the dashboard's "who is inside right now" query.
        Index(
            "ix_visitors_active_status",
            "status",
            postgresql_where=text("status <> 'EXITED'"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Visitor {self.visitor_code} status={self.status}>"
