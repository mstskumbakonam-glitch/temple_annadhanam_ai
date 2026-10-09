"""Visitor read logic.

Visitors are created by the AI pipeline in later phases, so this module exposes
queries only.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import Camera, Seat, Visitor, VisitorEvent
from app.models.enums import VisitorStatus
from app.services.exceptions import NotFoundError
from app.services.pagination import PageResult, paginate
from app.utils.time import utc_now


def _base_query() -> Select:
    """Visitors with camera and seat eagerly loaded, newest first."""
    return (
        select(Visitor)
        .options(joinedload(Visitor.camera), joinedload(Visitor.current_seat))
        .order_by(Visitor.id.desc())
    )


def utc_day_bounds(moment: datetime | None = None) -> tuple[datetime, datetime]:
    """Start and end of the UTC day containing `moment`.

    The system stores UTC internally, so "today" is defined in UTC.
    """
    now = moment or utc_now()
    start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def get_by_code(session: Session, visitor_code: str) -> Visitor:
    visitor = session.scalar(
        _base_query().where(Visitor.visitor_code == visitor_code)
    )
    if visitor is None:
        raise NotFoundError(f"Visitor '{visitor_code}' not found.")
    return visitor


def list_visitors(
    session: Session,
    *,
    page: int,
    page_size: int,
    status: str | None = None,
    camera_id: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> PageResult:
    statement = _base_query()
    if status is not None:
        statement = statement.where(Visitor.status == status)
    if camera_id is not None:
        statement = statement.join(Camera, Visitor.camera_id == Camera.id).where(
            Camera.camera_id == camera_id
        )
    if start_time is not None:
        statement = statement.where(Visitor.entry_time >= start_time)
    if end_time is not None:
        statement = statement.where(Visitor.entry_time < end_time)
    return paginate(session, statement, page, page_size)


def list_current(session: Session, *, page: int, page_size: int) -> PageResult:
    """Visitors still inside: anything not yet marked EXITED."""
    statement = _base_query().where(Visitor.status != VisitorStatus.EXITED)
    return paginate(session, statement, page, page_size)


def list_today(session: Session, *, page: int, page_size: int) -> PageResult:
    """Visitors who entered or left during the current UTC day."""
    start, end = utc_day_bounds()
    statement = _base_query().where(
        or_(
            Visitor.entry_time.between(start, end),
            Visitor.exit_time.between(start, end),
        )
    )
    return paginate(session, statement, page, page_size)


def list_events(
    session: Session, visitor_code: str, *, page: int, page_size: int
) -> PageResult:
    """Timeline for one visitor. Raises NotFoundError if the visitor is unknown."""
    visitor = get_by_code(session, visitor_code)
    statement = (
        select(VisitorEvent)
        .options(joinedload(VisitorEvent.camera))
        .where(VisitorEvent.visitor_id == visitor.id)
        .order_by(VisitorEvent.event_time.desc(), VisitorEvent.id.desc())
    )
    return paginate(session, statement, page, page_size)


def count_current(session: Session) -> int:
    from sqlalchemy import func

    return session.scalar(
        select(func.count()).select_from(Visitor).where(Visitor.status != VisitorStatus.EXITED)
    ) or 0
