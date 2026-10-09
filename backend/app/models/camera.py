"""Camera model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, bigint_pk
from app.models.enums import CameraStatus, values

if TYPE_CHECKING:
    from app.models.events import CameraEvent, VisitorEvent
    from app.models.occupancy import SeatOccupancy
    from app.models.seat import Seat
    from app.models.staff import StaffAttendance
    from app.models.visitor import Visitor


class Camera(Base, TimestampMixin):
    """A CCTV camera, e.g. ANN-ENT-01 or ANN-HALL-01.

    Camera IDs are configurable data, not hard-coded values.
    """

    __tablename__ = "cameras"

    id: Mapped[int] = bigint_pk()

    # Human-facing identifier used across the system (ANN-ENT-01).
    camera_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    camera_name: Mapped[str] = mapped_column(String(128), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))

    # Credentials belong in the URL only when unavoidable; see rtsp_url_masked.
    rtsp_url: Mapped[str | None] = mapped_column(String(512))

    enabled: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'OFFLINE'")
    )
    fps: Mapped[float | None] = mapped_column()
    last_frame_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Relationships (no cascade delete: history must survive) ---
    visitors: Mapped[list[Visitor]] = relationship(back_populates="camera")
    seats: Mapped[list[Seat]] = relationship(back_populates="camera")
    attendance_records: Mapped[list[StaffAttendance]] = relationship(back_populates="camera")
    seat_occupancies: Mapped[list[SeatOccupancy]] = relationship(back_populates="camera")
    visitor_events: Mapped[list[VisitorEvent]] = relationship(back_populates="camera")
    camera_events: Mapped[list[CameraEvent]] = relationship(back_populates="camera")

    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(v) for v in values(CameraStatus))})",
            name="status_valid",
        ),
        CheckConstraint("fps IS NULL OR fps >= 0", name="fps_non_negative"),
        Index("ix_cameras_status", "status"),
        Index("ix_cameras_enabled", "enabled"),
    )

    @property
    def rtsp_url_masked(self) -> str | None:
        """RTSP URL with any password replaced, for logs and API responses."""
        if not self.rtsp_url or "://" not in self.rtsp_url:
            return self.rtsp_url
        scheme, rest = self.rtsp_url.split("://", 1)
        if "@" not in rest:
            return self.rtsp_url
        credentials, host = rest.rsplit("@", 1)
        if ":" in credentials:
            user, _ = credentials.split(":", 1)
            credentials = f"{user}:***"
        return f"{scheme}://{credentials}@{host}"

    def __repr__(self) -> str:
        return f"<Camera {self.camera_id} status={self.status}>"
