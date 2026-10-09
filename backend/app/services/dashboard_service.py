"""Dashboard and hall occupancy aggregation.

Every figure is computed from PostgreSQL; nothing is hard-coded. With an empty
database all counters are legitimately zero.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Camera, Visitor
from app.models.enums import CameraStatus
from app.schemas.dashboard import DashboardSummary, HallOccupancy
from app.services import seat_service, staff_service, visitor_service


def _camera_counts(session: Session) -> tuple[int, int, int]:
    """(total, online, offline). Offline covers OFFLINE, ERROR and DISABLED."""
    rows = session.execute(
        select(Camera.status, func.count()).group_by(Camera.status)
    ).all()
    by_status = {status: count for status, count in rows}
    total = sum(by_status.values())
    online = by_status.get(CameraStatus.ONLINE, 0)
    return total, online, total - online


def _today_entry_exit_counts(session: Session) -> tuple[int, int]:
    start, end = visitor_service.utc_day_bounds()
    entries = session.scalar(
        select(func.count())
        .select_from(Visitor)
        .where(Visitor.entry_time >= start, Visitor.entry_time < end)
    ) or 0
    exits = session.scalar(
        select(func.count())
        .select_from(Visitor)
        .where(Visitor.exit_time >= start, Visitor.exit_time < end)
    ) or 0
    return entries, exits


def get_summary(session: Session) -> DashboardSummary:
    total_cameras, online_cameras, offline_cameras = _camera_counts(session)
    today_entries, today_exits = _today_entry_exit_counts(session)
    total_seats, occupied_seats = seat_service.occupancy_counts(session)

    return DashboardSummary(
        total_cameras=total_cameras,
        online_cameras=online_cameras,
        offline_cameras=offline_cameras,
        current_visitors=visitor_service.count_current(session),
        today_entries=today_entries,
        today_exits=today_exits,
        staff_present=staff_service.count_present(session),
        total_seats=total_seats,
        occupied_seats=occupied_seats,
        empty_seats=total_seats - occupied_seats,
        occupancy_percentage=seat_service.occupancy_percentage(total_seats, occupied_seats),
    )


def get_hall_occupancy(session: Session, hall_id: str) -> HallOccupancy:
    """Occupancy for one hall.

    An unknown hall is not an error: it simply has no seats, so every counter is
    zero and the percentage is 0.0 rather than a division by zero.
    """
    total, occupied = seat_service.occupancy_counts(session, hall_id=hall_id)
    return HallOccupancy(
        hall_id=hall_id,
        total_seats=total,
        occupied_seats=occupied,
        empty_seats=total - occupied,
        occupancy_percentage=seat_service.occupancy_percentage(total, occupied),
    )
