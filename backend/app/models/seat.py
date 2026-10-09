"""Seat model."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, bigint_pk

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.occupancy import SeatOccupancy


class Seat(Base, TimestampMixin):
    """A physical seat (S01, S02, ...) with a configurable detection polygon.

    Polygon coordinates live in the database, never hard-coded in AI code.
    """

    __tablename__ = "seats"

    id: Mapped[int] = bigint_pk()

    seat_id: Mapped[str] = mapped_column(String(32), nullable=False)
    hall_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'MAIN'")
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

    camera: Mapped[Camera | None] = relationship(back_populates="seats")
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
    )

    def __repr__(self) -> str:
        return f"<Seat {self.seat_id} hall={self.hall_id}>"
