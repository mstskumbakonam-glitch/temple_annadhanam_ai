"""Temples and their annadhanam halls.

One temple has zero or more halls; every hall belongs to exactly one temple.
Seat capacity is NOT stored on either table: it is always counted from the
`seats` rows, so it can never disagree with the seat list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, bigint_pk

if TYPE_CHECKING:
    from app.models.schedule import AnnadhanamSession
    from app.models.seat import Seat


class Temple(Base, TimestampMixin):
    __tablename__ = "temples"

    id: Mapped[int] = bigint_pk()
    temple_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    address: Mapped[str | None] = mapped_column(String(512))
    district: Mapped[str | None] = mapped_column(String(80))
    contact_phone: Mapped[str | None] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    # Sample rows created by the demo seed carry this flag and are labelled in the UI.
    is_demo: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    halls: Mapped[list[AnnadhanamHall]] = relationship(
        back_populates="temple", order_by="AnnadhanamHall.hall_code"
    )

    __table_args__ = (
        Index("ix_temples_district", "district"),
        Index("ix_temples_active", "active"),
        Index("ix_temples_name_lower", text("lower(name)")),
    )

    def __repr__(self) -> str:
        return f"<Temple {self.temple_code}>"


class AnnadhanamHall(Base, TimestampMixin):
    __tablename__ = "annadhanam_halls"

    id: Mapped[int] = bigint_pk()
    # Globally unique code. seats.hall_id references it (historic naming: the
    # seat table always stored the hall CODE, e.g. 'MAIN').
    hall_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    temple_id: Mapped[int] = mapped_column(
        ForeignKey("temples.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    building: Mapped[str | None] = mapped_column(String(120))
    floor: Mapped[str | None] = mapped_column(String(40))
    location_note: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    is_demo: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    temple: Mapped[Temple] = relationship(back_populates="halls")
    seats: Mapped[list[Seat]] = relationship(
        primaryjoin="AnnadhanamHall.hall_code == foreign(Seat.hall_id)",
        viewonly=True,
    )
    sessions: Mapped[list[AnnadhanamSession]] = relationship(back_populates="hall")

    __table_args__ = (
        Index("ix_annadhanam_halls_active", "active"),
        CheckConstraint("length(name) > 0", name="name_not_empty"),
    )

    def __repr__(self) -> str:
        return f"<AnnadhanamHall {self.hall_code}>"
