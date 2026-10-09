"""Annadhanam sessions (meal services) scheduled in a hall.

Overlapping sessions in the same hall are impossible: an EXCLUDE constraint on
(hall, time range) rejects them inside PostgreSQL, so two operators saving at
the same moment cannot both succeed. Cancelled sessions free their slot.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, bigint_pk
from app.models.enums import MealType, SessionStatus, values

if TYPE_CHECKING:
    from app.models.staff import Staff
    from app.models.temple import AnnadhanamHall


class AnnadhanamSession(Base, TimestampMixin):
    __tablename__ = "annadhanam_sessions"

    id: Mapped[int] = bigint_pk()
    hall_id: Mapped[int] = mapped_column(
        ForeignKey("annadhanam_halls.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    meal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Local calendar date (SITE_TIMEZONE) for filtering; the instants are UTC.
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expected_devotees: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Recorded after (or during) the service; NULL means "not recorded", never 0.
    actual_devotees: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'SCHEDULED'")
    )
    responsible_staff_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL"), index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)
    cancel_reason: Mapped[str | None] = mapped_column(String(255))
    is_demo: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    hall: Mapped[AnnadhanamHall] = relationship(back_populates="sessions")
    responsible_staff: Mapped[Staff | None] = relationship()

    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ends_after_starts"),
        CheckConstraint("expected_devotees >= 0", name="expected_non_negative"),
        CheckConstraint("actual_devotees IS NULL OR actual_devotees >= 0", name="actual_non_negative"),
        CheckConstraint(
            f"status IN ({', '.join(repr(v) for v in values(SessionStatus))})", name="status_valid"
        ),
        CheckConstraint(
            f"meal_type IN ({', '.join(repr(v) for v in values(MealType))})", name="meal_type_valid"
        ),
        CheckConstraint(
            "(status = 'CANCELLED') = (cancel_reason IS NOT NULL)", name="cancel_reason_matches_status"
        ),
        ExcludeConstraint(
            ("hall_id", "="),
            (text("tstzrange(starts_at, ends_at, '[)')"), "&&"),
            name="no_overlapping_sessions",
            using="gist",
            where=text("status <> 'CANCELLED'"),
        ),
        Index("ix_annadhanam_sessions_session_date", "session_date"),
        Index("ix_annadhanam_sessions_starts_at", "starts_at"),
        Index("ix_annadhanam_sessions_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<AnnadhanamSession {self.id} {self.session_date} {self.status}>"
