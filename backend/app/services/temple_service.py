"""Temples, halls and seat counting.

Seat counting rules (single source of truth, used by every screen and report):

* only ENABLED seats exist for counting purposes ("installed");
* capacity  = installed seats that are not OUT_OF_SERVICE;
* available = seats whose status is AVAILABLE (= capacity - occupied - reserved);
* temple and dashboard totals include only ACTIVE halls of ACTIVE temples;
  a hall's own figures are always shown, active or not.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import AnnadhanamHall, AnnadhanamSession, Seat, Staff, Temple
from app.models.enums import SeatStatus
from app.schemas.management import (
    HallCreate,
    HallRead,
    HallUpdate,
    SeatCounts,
    TempleCreate,
    TempleRead,
    TempleUpdate,
)
from app.services.exceptions import ConflictError, NotFoundError
from app.services.pagination import PageResult, paginate


# ---------------------------------------------------------------------------
# Seat counting
# ---------------------------------------------------------------------------
def make_counts(by_status: dict[str, int]) -> SeatCounts:
    occupied = by_status.get(SeatStatus.OCCUPIED, 0)
    reserved = by_status.get(SeatStatus.RESERVED, 0)
    available = by_status.get(SeatStatus.AVAILABLE, 0)
    out = by_status.get(SeatStatus.OUT_OF_SERVICE, 0)
    capacity = available + occupied + reserved
    return SeatCounts(
        installed=capacity + out, capacity=capacity, available=available, occupied=occupied,
        reserved=reserved, out_of_service=out,
        occupancy_percentage=round(occupied / capacity * 100, 2) if capacity else 0.0,
    )


def add_counts(items: Iterable[SeatCounts]) -> SeatCounts:
    total: dict[str, int] = defaultdict(int)
    for c in items:
        total[SeatStatus.AVAILABLE] += c.available
        total[SeatStatus.OCCUPIED] += c.occupied
        total[SeatStatus.RESERVED] += c.reserved
        total[SeatStatus.OUT_OF_SERVICE] += c.out_of_service
    return make_counts(total)


def seat_counts_by_hall(session: Session, hall_codes: Iterable[str] | None = None) -> dict[str, SeatCounts]:
    """{hall_code: counts} for enabled seats, computed in one GROUP BY."""
    stmt = (select(Seat.hall_id, Seat.status, func.count())
            .where(Seat.enabled.is_(True))
            .group_by(Seat.hall_id, Seat.status))
    if hall_codes is not None:
        codes = list(hall_codes)
        if not codes:
            return {}
        stmt = stmt.where(Seat.hall_id.in_(codes))
    raw: dict[str, dict[str, int]] = defaultdict(dict)
    for hall_code, status, count in session.execute(stmt):
        raw[hall_code][status] = count
    return {code: make_counts(by) for code, by in raw.items()}


def counts_for_halls(counts: dict[str, SeatCounts], halls: Iterable[AnnadhanamHall],
                     active_only: bool) -> SeatCounts:
    return add_counts(counts.get(h.hall_code, SeatCounts()) for h in halls
                      if not active_only or h.active)


# ---------------------------------------------------------------------------
# Temples
# ---------------------------------------------------------------------------
def get_temple(session: Session, temple_code: str) -> Temple:
    temple = session.scalar(select(Temple).options(selectinload(Temple.halls))
                            .where(Temple.temple_code == temple_code))
    if temple is None:
        raise NotFoundError(f"Temple '{temple_code}' not found.")
    return temple


def temple_read(session: Session, temple: Temple, counts: dict[str, SeatCounts] | None = None) -> TempleRead:
    if counts is None:
        counts = seat_counts_by_hall(session, [h.hall_code for h in temple.halls])
    return TempleRead(
        temple_code=temple.temple_code, name=temple.name, address=temple.address,
        district=temple.district, contact_phone=temple.contact_phone, active=temple.active,
        is_demo=temple.is_demo, hall_count=len(temple.halls),
        seats=counts_for_halls(counts, temple.halls, active_only=True),
        created_at=temple.created_at, updated_at=temple.updated_at,
    )


def list_temples(session: Session, *, page: int, page_size: int, search: str | None = None,
                 district: str | None = None, active: bool | None = None) -> tuple[list[TempleRead], PageResult]:
    stmt: Select = select(Temple).options(selectinload(Temple.halls)).order_by(Temple.name, Temple.temple_code)
    if search:
        like = f"%{search.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Temple.name).like(like), func.lower(Temple.temple_code).like(like)))
    if district:
        stmt = stmt.where(func.lower(Temple.district) == district.strip().lower())
    if active is not None:
        stmt = stmt.where(Temple.active.is_(active))
    result = paginate(session, stmt, page, page_size)
    codes = [h.hall_code for t in result.items for h in t.halls]
    counts = seat_counts_by_hall(session, codes)
    return [temple_read(session, t, counts) for t in result.items], result


def districts(session: Session) -> list[str]:
    return [d for (d,) in session.execute(
        select(Temple.district).where(Temple.district.is_not(None)).distinct().order_by(Temple.district))]


def create_temple(session: Session, payload: TempleCreate) -> Temple:
    temple = Temple(**payload.model_dump())
    session.add(temple)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(f"Temple code '{payload.temple_code}' already exists.") from exc
    return get_temple(session, temple.temple_code)


def update_temple(session: Session, temple_code: str, payload: TempleUpdate) -> Temple:
    temple = get_temple(session, temple_code)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field in ("name",) and value is None:
            continue
        setattr(temple, field, value)
    session.commit()
    return get_temple(session, temple_code)


def delete_temple(session: Session, temple_code: str) -> None:
    temple = get_temple(session, temple_code)
    if temple.halls:
        raise ConflictError(
            f"Temple '{temple_code}' still has {len(temple.halls)} hall(s). "
            "Delete or move them first, or mark the temple inactive.")
    session.execute(Staff.__table__.update().where(Staff.temple_id == temple.id).values(temple_id=None))
    session.delete(temple)
    session.commit()


# ---------------------------------------------------------------------------
# Halls
# ---------------------------------------------------------------------------
def get_hall(session: Session, hall_code: str) -> AnnadhanamHall:
    hall = session.scalar(select(AnnadhanamHall).options(joinedload(AnnadhanamHall.temple))
                          .where(AnnadhanamHall.hall_code == hall_code))
    if hall is None:
        raise NotFoundError(f"Hall '{hall_code}' not found.")
    return hall


def hall_read(hall: AnnadhanamHall, counts: dict[str, SeatCounts]) -> HallRead:
    return HallRead(
        hall_code=hall.hall_code, temple_code=hall.temple.temple_code, temple_name=hall.temple.name,
        name=hall.name, building=hall.building, floor=hall.floor, location_note=hall.location_note,
        active=hall.active, is_demo=hall.is_demo, seats=counts.get(hall.hall_code, SeatCounts()),
        created_at=hall.created_at, updated_at=hall.updated_at,
    )


def list_halls(session: Session, *, page: int, page_size: int, temple_code: str | None = None,
               search: str | None = None, active: bool | None = None) -> tuple[list[HallRead], PageResult]:
    stmt = (select(AnnadhanamHall).join(Temple, AnnadhanamHall.temple_id == Temple.id)
            .options(joinedload(AnnadhanamHall.temple))
            .order_by(Temple.name, AnnadhanamHall.name))
    if temple_code:
        stmt = stmt.where(Temple.temple_code == temple_code)
    if search:
        like = f"%{search.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(AnnadhanamHall.name).like(like),
                              func.lower(AnnadhanamHall.hall_code).like(like)))
    if active is not None:
        stmt = stmt.where(AnnadhanamHall.active.is_(active))
    result = paginate(session, stmt, page, page_size)
    counts = seat_counts_by_hall(session, [h.hall_code for h in result.items])
    return [hall_read(h, counts) for h in result.items], result


def create_hall(session: Session, payload: HallCreate) -> HallRead:
    temple = get_temple(session, payload.temple_code)
    data = payload.model_dump(exclude={"temple_code"})
    hall = AnnadhanamHall(temple_id=temple.id, **data)
    session.add(hall)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(f"Hall code '{payload.hall_code}' already exists.") from exc
    return hall_read(get_hall(session, payload.hall_code), {})


def update_hall(session: Session, hall_code: str, payload: HallUpdate) -> HallRead:
    hall = get_hall(session, hall_code)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("temple_code"):
        hall.temple_id = get_temple(session, changes.pop("temple_code")).id
    changes.pop("temple_code", None)
    for field, value in changes.items():
        if field == "name" and value is None:
            continue
        setattr(hall, field, value)
    session.commit()
    session.expire_all()
    hall = get_hall(session, hall_code)
    return hall_read(hall, seat_counts_by_hall(session, [hall_code]))


def delete_hall(session: Session, hall_code: str) -> None:
    hall = get_hall(session, hall_code)
    seats = session.scalar(select(func.count()).select_from(Seat).where(Seat.hall_id == hall_code)) or 0
    sessions = session.scalar(select(func.count()).select_from(AnnadhanamSession)
                              .where(AnnadhanamSession.hall_id == hall.id)) or 0
    if seats or sessions:
        raise ConflictError(
            f"Hall '{hall_code}' has {seats} seat(s) and {sessions} session(s). "
            "Mark it inactive instead of deleting it.")
    session.execute(Staff.__table__.update().where(Staff.hall_id == hall.id).values(hall_id=None))
    session.delete(hall)
    session.commit()
