"""Seat model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, bigint_pk
from app.models.enums import SeatStatus, SeatStatusSource, values

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.occupancy import SeatOccupancy
    from app.models.schedule import AnnadhanamSession
    from app.models.temple import AnnadhanamHall


class Seat(Base, TimestampMixin):
    """A physical seat (S01, S02, ...) with a configurable detection polygon.

    Polygon coordinates live in the database, never hard-coded in AI code.
    """

    __tablename__ = "seats"

    id: Mapped[int] = bigint_pk()

    seat_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # The hall CODE (annadhanam_halls.hall_code). Codes are kept in sync by
    # ON UPDATE CASCADE; a hall with seats cannot be deleted.
    hall_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("annadhanam_halls.hall_code", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    seat_label: Mapped[str | None] = mapped_column(String(64))
    row_number: Mapped[int | None] = mapped_column(Integer)
    column_number: Mapped[int | None] = mapped_column(Integer)

    # The camera that observes this seat. Kept if the camera is removed.
    camera_id: Mapped[int | None] = mapped_column(
        ForeignKey("cameras.id", ondelete="SET NULL"), index=True
    )

    # [{"x": 100, "y": 200}, ...] in pixel coordinates of the owning camera frame.
    # none_as_null=True so a Python None becomes SQL NULL rather than JSON 'null';
    # without it jsonb_typeof() returns 'null' and the polygon CHECK constraint
    # rejects every seat created without a polygon.
    polygon_points: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB(none_as_null=True)
    )

    enabled: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    # ---- Confirmed status (set by people, never directly by AI detections) ----
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'AVAILABLE'")
    )
    status_source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'MANUAL'")
    )
    status_since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    reservation_ref: Mapped[str | None] = mapped_column(String(64))
    reserved_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("annadhanam_sessions.id", ondelete="SET NULL"), index=True
    )
    # Incremented on every status change: clients may send the version they saw,
    # and a stale write is rejected instead of silently overwriting someone else.
    status_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    camera: Mapped[Camera | None] = relationship(back_populates="seats")
    hall: Mapped[AnnadhanamHall] = relationship(
        primaryjoin="foreign(Seat.hall_id) == AnnadhanamHall.hall_code", viewonly=True
    )
    reserved_session: Mapped[AnnadhanamSession | None] = relationship()
    occupancies: Mapped[list[SeatOccupancy]] = relationship(back_populates="seat")

    __table_args__ = (
        # Seat IDs are unique per hall, so a second hall may also have an S01.
        UniqueConstraint("hall_id", "seat_id", name="hall_id_seat_id"),
        CheckConstraint(
            "polygon_points IS NULL OR jsonb_typeof(polygon_points) = 'array'",
            name="polygon_points_is_array",
        ),
        CheckConstraint(
            "row_number IS NULL OR row_number >= 0", name="row_number_non_negative"
        ),
        CheckConstraint(
            "column_number IS NULL OR column_number >= 0",
            name="column_number_non_negative",
        ),
        Index("ix_seats_seat_id", "seat_id"),
        Index("ix_seats_hall_id", "hall_id"),
        Index("ix_seats_enabled", "enabled"),
        Index("ix_seats_hall_id_status", "hall_id", "status"),
        CheckConstraint(
            f"status IN ({', '.join(repr(v) for v in values(SeatStatus))})",
            name="status_valid",
        ),
        CheckConstraint(
            f"status_source IN ({', '.join(repr(v) for v in values(SeatStatusSource))})",
            name="status_source_valid",
        ),
        # Reservation details only exist while the seat is reserved.
        CheckConstraint(
            "status = 'RESERVED' OR (reservation_ref IS NULL AND reserved_session_id IS NULL)",
            name="reservation_only_when_reserved",
        ),
    )

    def __repr__(self) -> str:
        return f"<Seat {self.seat_id} hall={self.hall_id}>"


class SeatStatusEvent(Base, CreatedAtMixin):
    """Append-only history of confirmed seat status changes (for reports)."""

    __tablename__ = "seat_status_events"

    id: Mapped[int] = bigint_pk()
    seat_id: Mapped[int] = mapped_column(
        ForeignKey("seats.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hall_id: Mapped[str] = mapped_column(String(64), nullable=False)   # denormalised for reports
    from_status: Mapped[str] = mapped_column(String(16), nullable=False)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_role: Mapped[str | None] = mapped_column(String(16))
    reservation_ref: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("annadhanam_sessions.id", ondelete="SET NULL")
    )
    # Seconds the seat spent in from_status (e.g. how long it was occupied).
    previous_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        CheckConstraint(
            f"to_status IN ({', '.join(repr(v) for v in values(SeatStatus))})",
            name="to_status_valid",
        ),
        Index("ix_seat_status_events_changed_at", "changed_at"),
        Index("ix_seat_status_events_hall_id_changed_at", "hall_id", "changed_at"),
    )
