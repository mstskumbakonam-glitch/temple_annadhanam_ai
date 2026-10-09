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
from app.services import staff_service, visitor_service


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
    from app.services.temple_service import add_counts, seat_counts_by_hall

    seats = add_counts(seat_counts_by_hall(session).values())

    return DashboardSummary(
        total_cameras=total_cameras,
        online_cameras=online_cameras,
        offline_cameras=offline_cameras,
        current_visitors=visitor_service.count_current(session),
        today_entries=today_entries,
        today_exits=today_exits,
        staff_present=staff_service.count_present(session),
        total_seats=seats.capacity,
        occupied_seats=seats.occupied,
        reserved_seats=seats.reserved,
        empty_seats=seats.available,
        occupancy_percentage=seats.occupancy_percentage,
    )


def get_hall_occupancy(session: Session, hall_id: str) -> HallOccupancy:
    """Occupancy for one hall.

    An unknown hall is not an error: it simply has no seats, so every counter is
    zero and the percentage is 0.0 rather than a division by zero.
    """
    from app.services.temple_service import SeatCounts, seat_counts_by_hall

    c = seat_counts_by_hall(session, [hall_id]).get(hall_id, SeatCounts())
    return HallOccupancy(
        hall_id=hall_id,
        total_seats=c.capacity,
        occupied_seats=c.occupied,
        reserved_seats=c.reserved,
        empty_seats=c.available,
        occupancy_percentage=c.occupancy_percentage,
    )
