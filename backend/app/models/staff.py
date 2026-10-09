"""Staff and staff attendance models.

Face embeddings are stored only for registered staff. Unknown faces are never
persisted and are never assigned a staff code.
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
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, bigint_pk
from app.models.enums import AttendanceStatus, DailyAttendanceStatus, StaffShift, values
from app.models.sequences import staff_code_default

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.occupancy import SeatOccupancy
    from app.models.temple import AnnadhanamHall, Temple


class Staff(Base, TimestampMixin):
    """A registered staff member (STAFF-001)."""

    __tablename__ = "staff"

    id: Mapped[int] = bigint_pk()

    staff_code: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        unique=True,
        server_default=staff_code_default(),
    )
    staff_name: Mapped[str] = mapped_column(String(128), nullable=False)

    # Optional payroll/HR reference. Unique when present; many rows may be NULL,
    # which PostgreSQL permits under a UNIQUE constraint.
    employee_code: Mapped[str | None] = mapped_column(String(64), unique=True)
    department: Mapped[str | None] = mapped_column(String(128))

    # Raw float32 vector as BYTEA, not JSON text: compact and exact to round-trip.
    face_embedding: Mapped[bytes | None] = mapped_column(LargeBinary)

    active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    # ---- Management fields ----
    phone: Mapped[str | None] = mapped_column(String(32))          # admin-only in the API
    designation: Mapped[str | None] = mapped_column(String(80))
    shift: Mapped[str | None] = mapped_column(String(16))
    temple_id: Mapped[int | None] = mapped_column(
        ForeignKey("temples.id", ondelete="SET NULL"), index=True
    )
    hall_id: Mapped[int | None] = mapped_column(
        ForeignKey("annadhanam_halls.id", ondelete="SET NULL"), index=True
    )
    is_demo: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    temple: Mapped[Temple | None] = relationship()
    hall: Mapped[AnnadhanamHall | None] = relationship()
    daily_attendance: Mapped[list[StaffDailyAttendance]] = relationship(back_populates="staff")

    attendance_records: Mapped[list[StaffAttendance]] = relationship(back_populates="staff")
    seat_occupancies: Mapped[list[SeatOccupancy]] = relationship(back_populates="staff")

    __table_args__ = (
        Index("ix_staff_active", "active"),
        Index("ix_staff_department", "department"),
        CheckConstraint(
            f"shift IS NULL OR shift IN ({', '.join(repr(v) for v in values(StaffShift))})",
            name="shift_valid",
        ),
    )

    def __repr__(self) -> str:
        return f"<Staff {self.staff_code} {self.staff_name}>"


class StaffAttendance(Base, TimestampMixin):
    """One presence session for a staff member."""

    __tablename__ = "staff_attendance"

    id: Mapped[int] = bigint_pk()

    # RESTRICT: attendance history must not disappear with the staff record.
    staff_id: Mapped[int] = mapped_column(
        ForeignKey("staff.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    camera_id: Mapped[int | None] = mapped_column(
        ForeignKey("cameras.id", ondelete="SET NULL"), index=True
    )

    entry_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PRESENT'")
    )

    staff: Mapped[Staff] = relationship(back_populates="attendance_records")
    camera: Mapped[Camera | None] = relationship(back_populates="attendance_records")

    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(v) for v in values(AttendanceStatus))})",
            name="status_valid",
        ),
        CheckConstraint(
            "exit_time IS NULL OR exit_time >= entry_time", name="exit_after_entry"
        ),
        CheckConstraint(
            "(status = 'PRESENT' AND exit_time IS NULL) "
            "OR (status = 'EXITED' AND exit_time IS NOT NULL)",
            name="exit_time_matches_status",
        ),
        # One open attendance row per staff member: the database, not the
        # application, prevents a duplicate record being created on every frame.
        Index(
            "uq_staff_attendance_active",
            "staff_id",
            unique=True,
            postgresql_where=text("status = 'PRESENT'"),
        ),
        Index("ix_staff_attendance_entry_time", "entry_time"),
        Index("ix_staff_attendance_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<StaffAttendance staff_id={self.staff_id} status={self.status}>"


class StaffDailyAttendance(Base, TimestampMixin):
    """The daily attendance register, marked by a supervisor (one row per person per day).

    Separate from StaffAttendance (camera-observed presence sessions). A staff member
    with NO row for a day is "not marked" - never silently counted as absent.
    """

    __tablename__ = "staff_daily_attendance"

    id: Mapped[int] = bigint_pk()
    staff_id: Mapped[int] = mapped_column(
        ForeignKey("staff.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'MANUAL'"))

    staff: Mapped[Staff] = relationship(back_populates="daily_attendance")

    __table_args__ = (
        UniqueConstraint("staff_id", "attendance_date"),
        CheckConstraint(
            f"status IN ({', '.join(repr(v) for v in values(DailyAttendanceStatus))})",
            name="status_valid",
        ),
        CheckConstraint(
            "check_out_at IS NULL OR check_in_at IS NULL OR check_out_at >= check_in_at",
            name="check_out_after_check_in",
        ),
        Index("ix_staff_daily_attendance_date", "attendance_date"),
    )
