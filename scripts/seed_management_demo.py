#!/usr/bin/env python3
"""DEMO ONLY: clearly labelled SAMPLE temples, halls, seats, staff and sessions.

Nothing here is a real temple or a real person. Every row is flagged is_demo=true,
names say "Sample", and the dashboard shows a "Sample data" badge. Runs only when
DEMO_MODE=true and APP_ENV is not production.

Usage (from backend/, virtualenv active):
    python ../scripts/seed_management_demo.py           # create (idempotent)
    python ../scripts/seed_management_demo.py --reset   # remove all sample rows, then recreate
    python ../scripts/seed_management_demo.py --remove  # remove all sample rows only
"""

from __future__ import annotations

import argparse
import sys
from datetime import time, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select, text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import AnnadhanamHall, AnnadhanamSession, Seat, Staff, StaffDailyAttendance, Temple  # noqa: E402
from app.services.session_service import instants, local_today  # noqa: E402
from app.utils.time import utc_now  # noqa: E402

TEMPLES = [
    ("DEMO-T1", "Sample Temple A (demo)", "Sample District 1",
     [("DEMO-T1-H1", "Main Annadhanam Hall", "Ground floor", 6, 12),
      ("DEMO-T1-H2", "Upper Dining Hall", "First floor", 4, 10)]),
    ("DEMO-T2", "Sample Temple B (demo)", "Sample District 2",
     [("DEMO-T2-H1", "Annadhanam Hall", "Ground floor", 5, 10)]),
]
STAFF = [("Sample Staff 1", "Supervisor", "MORNING", "DEMO-T1", "DEMO-T1-H1"),
         ("Sample Staff 2", "Cook", "MORNING", "DEMO-T1", "DEMO-T1-H1"),
         ("Sample Staff 3", "Server", "AFTERNOON", "DEMO-T1", "DEMO-T1-H2"),
         ("Sample Staff 4", "Cleaner", "EVENING", "DEMO-T1", None),
         ("Sample Staff 5", "Supervisor", "FULL_DAY", "DEMO-T2", "DEMO-T2-H1"),
         ("Sample Staff 6", "Cook", "MORNING", "DEMO-T2", "DEMO-T2-H1")]


def reset(session) -> None:
    session.execute(text("DELETE FROM staff_daily_attendance WHERE staff_id IN (SELECT id FROM staff WHERE is_demo)"))
    session.execute(text("UPDATE seats SET reserved_session_id = NULL, reservation_ref = NULL, status = 'AVAILABLE' "
                         "WHERE reserved_session_id IN (SELECT id FROM annadhanam_sessions WHERE is_demo)"))
    session.execute(text("DELETE FROM annadhanam_sessions WHERE is_demo"))
    session.execute(text("DELETE FROM seats WHERE hall_id IN (SELECT hall_code FROM annadhanam_halls WHERE is_demo)"))
    session.execute(text("DELETE FROM staff WHERE is_demo"))
    session.execute(text("DELETE FROM annadhanam_halls WHERE is_demo"))
    session.execute(text("DELETE FROM temples WHERE is_demo"))
    session.commit()


def seed() -> None:
    settings = get_settings()
    tz = settings.site_timezone
    today = local_today(tz)
    with SessionLocal() as s:
        if s.scalar(select(Temple).where(Temple.temple_code == "DEMO-T1")):
            print("Sample data already present (use --reset to recreate).")
            return
        halls: dict[str, AnnadhanamHall] = {}
        temples: dict[str, Temple] = {}
        for code, name, district, hall_specs in TEMPLES:
            t = Temple(temple_code=code, name=name, district=district, address="Sample address (demo)",
                       is_demo=True)
            s.add(t)
            s.flush()
            temples[code] = t
            for h_code, h_name, floor, rows, cols in hall_specs:
                h = AnnadhanamHall(hall_code=h_code, temple_id=t.id, name=h_name, floor=floor, is_demo=True)
                s.add(h)
                s.flush()
                halls[h_code] = h
                n = 0
                for r in range(1, rows + 1):
                    for c in range(1, cols + 1):
                        n += 1
                        s.add(Seat(seat_id=f"S{n:03d}", hall_id=h_code, seat_label=f"Row {r} · Seat {c}",
                                   row_number=r, column_number=c))
        s.flush()

        staff = []
        for name, role, shift, t_code, h_code in STAFF:
            st = Staff(staff_name=name, designation=role, shift=shift, department="Annadhanam",
                       temple_id=temples[t_code].id, hall_id=halls[h_code].id if h_code else None, is_demo=True)
            s.add(st)
            staff.append(st)
        s.flush()

        def session_at(hall, name, meal, day, start, end, expected, status="SCHEDULED", actual=None):
            a, b = instants(day, start, end, tz)
            s.add(AnnadhanamSession(hall_id=halls[hall].id, name=name, meal_type=meal, session_date=day,
                                    starts_at=a, ends_at=b, expected_devotees=expected, status=status,
                                    actual_devotees=actual, responsible_staff_id=staff[0].id, is_demo=True,
                                    notes="Sample session (demo)"))

        for d in range(-3, 0):            # completed history with recorded attendance
            day = today + timedelta(days=d)
            session_at("DEMO-T1-H1", "Noon annadhanam", "LUNCH", day, time(11, 30), time(14, 0), 150,
                       "COMPLETED", 140 + 7 * d)
        session_at("DEMO-T1-H1", "Morning prasadam", "BREAKFAST", today, time(7, 0), time(8, 30), 60)
        session_at("DEMO-T1-H1", "Noon annadhanam", "LUNCH", today, time(11, 30), time(14, 0), 150)
        session_at("DEMO-T1-H2", "Noon annadhanam (overflow)", "LUNCH", today, time(12, 0), time(14, 0), 60)
        session_at("DEMO-T2-H1", "Evening annadhanam", "DINNER", today, time(19, 0), time(20, 30), 70)
        for d in range(1, 6):
            day = today + timedelta(days=d)
            session_at("DEMO-T1-H1", "Noon annadhanam", "LUNCH", day, time(11, 30), time(14, 0), 150)
            session_at("DEMO-T2-H1", "Evening annadhanam", "DINNER", day, time(19, 0), time(20, 30), 70)

        # A few seat states so the hall views are not empty. Real use: operators set these.
        main = list(s.scalars(select(Seat).where(Seat.hall_id == "DEMO-T1-H1").order_by(Seat.seat_id)))
        now = utc_now()
        for seat in main[:30]:
            seat.status, seat.status_since = "OCCUPIED", now
        for seat in main[30:36]:
            seat.status, seat.reservation_ref = "RESERVED", "Elderly / differently-abled"
        for seat in main[-2:]:
            seat.status = "OUT_OF_SERVICE"

        for st, status in zip(staff[:5], ["PRESENT", "PRESENT", "HALF_DAY", "LEAVE", "PRESENT"]):
            s.add(StaffDailyAttendance(staff_id=st.id, attendance_date=today, status=status,
                                       note="Sample entry (demo)"))
        s.commit()
    print("Sample temples, halls, seats, staff and sessions created (all flagged is_demo).")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reset", action="store_true", help="remove sample rows, then recreate them")
    p.add_argument("--remove", action="store_true", help="remove sample rows and stop")
    args = p.parse_args()
    settings = get_settings()
    if settings.is_production:
        raise SystemExit("Refusing: APP_ENV is production.")
    if not settings.demo_mode and not args.remove:
        raise SystemExit("Refusing: sample data is only created when DEMO_MODE=true.")
    if args.reset or args.remove:
        with SessionLocal() as s:
            reset(s)
        print("Removed sample rows.")
        if args.remove:
            return 0
    seed()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
