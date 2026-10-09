"""Seat occupancy history."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, bigint_pk
from app.models.enums import OccupancyStatus, PersonType, values

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.seat import Seat
    from app.models.staff import Staff
    from app.models.visitor import Visitor


class SeatOccupancy(Base, CreatedAtMixin):
    """One occupancy period for a seat.

    A row refers to exactly one occupant: a visitor or a staff member, never
    both. This is enforced by a CHECK constraint rather than by application code.
    """

    __tablename__ = "seat_occupancy"

    id: Mapped[int] = bigint_pk()

    # RESTRICT everywhere: occupancy history is a record and must not vanish.
    seat_id: Mapped[int] = mapped_column(
        ForeignKey("seats.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    camera_id: Mapped[int | None] = mapped_column(
        ForeignKey("cameras.id", ondelete="SET NULL"), index=True
    )

    person_type: Mapped[str] = mapped_column(String(16), nullable=False)
    visitor_id: Mapped[int | None] = mapped_column(
        ForeignKey("visitors.id", ondelete="RESTRICT"), index=True
    )
    staff_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff.id", ondelete="RESTRICT"), index=True
    )

    occupied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'OCCUPIED'")
    )

    seat: Mapped[Seat] = relationship(back_populates="occupancies")
    camera: Mapped[Camera | None] = relationship(back_populates="seat_occupancies")
    visitor: Mapped[Visitor | None] = relationship(back_populates="seat_occupancies")
    staff: Mapped[Staff | None] = relationship(back_populates="seat_occupancies")

    __table_args__ = (
        CheckConstraint(
            f"person_type IN ({', '.join(repr(v) for v in values(PersonType))})",
            name="person_type_valid",
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(v) for v in values(OccupancyStatus))})",
            name="status_valid",
        ),
        # A VISITOR row carries visitor_id and no staff_id; a STAFF row the reverse.
        # This makes "occupied by both" unrepresentable.
        CheckConstraint(
            "(person_type = 'VISITOR' AND visitor_id IS NOT NULL AND staff_id IS NULL)"
            " OR "
            "(person_type = 'STAFF' AND staff_id IS NOT NULL AND visitor_id IS NULL)",
            name="occupant_matches_person_type",
        ),
        CheckConstraint(
            "released_at IS NULL OR released_at >= occupied_at",
            name="released_after_occupied",
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="duration_non_negative",
        ),
        CheckConstraint(
            "(status = 'OCCUPIED' AND released_at IS NULL) "
            "OR (status = 'RELEASED' AND released_at IS NOT NULL)",
            name="released_at_matches_status",
        ),
        # A seat can have only one open occupancy at a time.
        Index(
            "uq_seat_occupancy_active",
            "seat_id",
            unique=True,
            postgresql_where=text("status = 'OCCUPIED'"),
        ),
        Index("ix_seat_occupancy_occupied_at", "occupied_at"),
        Index("ix_seat_occupancy_status", "status"),
        Index("ix_seat_occupancy_person_type", "person_type"),
    )

    def __repr__(self) -> str:
        return f"<SeatOccupancy seat_id={self.seat_id} {self.person_type} {self.status}>"
