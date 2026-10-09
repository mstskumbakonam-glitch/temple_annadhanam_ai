"""Management dashboard: every figure is counted from PostgreSQL rows."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import CrowdAlert, Staff, Temple
from app.schemas.management import ManagementOverview, SessionSummary
from app.services import attendance_service, session_service
from app.services.temple_service import counts_for_halls, get_temple, seat_counts_by_hall
from app.utils.time import utc_now


def overview(session: Session, tz_name: str, temple_code: str | None = None) -> ManagementOverview:
    if temple_code:
        temples = [get_temple(session, temple_code)]
    else:
        temples = list(session.scalars(select(Temple).options(selectinload(Temple.halls))))
    halls = [h for t in temples for h in t.halls]
    # Totals use active halls of active temples only (rule in temple_service).
    usable = [h for t in temples if t.active for h in t.halls if h.active]
    counts = seat_counts_by_hall(session, [h.hall_code for h in usable])
    seats = counts_for_halls(counts, usable, active_only=True)

    staff_q = select(Staff.id).where(Staff.active.is_(True))
    if temple_code:
        staff_q = staff_q.where(Staff.temple_id == temples[0].id)
    staff_ids = list(session.scalars(staff_q))

    today = session_service.local_today(tz_name)
    hall_ids = [h.id for h in halls] if temple_code else None
    s = session_service.summary(session, tz_name, hall_ids)
    s.pop("today")
    todays = session_service.list_sessions(
        session, page=1, page_size=50, start_date=today, end_date=today, temple_code=temple_code)

    demo = session.scalar(select(func.count()).select_from(Temple).where(Temple.is_demo.is_(True))) or 0
    return ManagementOverview(
        generated_at=utc_now(), site_timezone=tz_name, local_date=today, temple_code=temple_code,
        temples_total=len(temples), temples_active=sum(1 for t in temples if t.active),
        halls_total=len(halls), halls_active=sum(1 for h in halls if h.active and h.temple.active),
        seats=seats,
        staff_total_active=len(staff_ids),
        attendance=attendance_service.summarize(session, today, staff_ids),
        sessions=SessionSummary(**s),
        todays_sessions=session_service.reads(session, todays.items, tz_name),
        active_alerts=None if temple_code else (session.scalar(
            select(func.count()).select_from(CrowdAlert).where(CrowdAlert.ended_at.is_(None))) or 0),
        demo_records_present=demo > 0,
    )
