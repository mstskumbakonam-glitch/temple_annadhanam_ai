#!/usr/bin/env python3
"""DEVELOPMENT ONLY seed data.

Inserts sample cameras and seats so the dashboard has something to show while
developing. This is NOT run by Alembic and must never run automatically: real
deployments configure their own cameras and seat polygons.

Usage (from backend/, with the virtualenv active):
    python ../scripts/seed_dev.py
    python ../scripts/seed_dev.py --reset   # delete seeded rows first

The polygon coordinates below are placeholders for a 1920x1080 frame. Replace
them with real values from your own camera calibration.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Camera, Seat  # noqa: E402

DEV_CAMERAS = [
    {"camera_id": "ANN-ENT-01", "camera_name": "Entrance", "location": "Main entrance"},
    {"camera_id": "ANN-HALL-01", "camera_name": "Hall North", "location": "Dining hall north"},
    {"camera_id": "ANN-HALL-02", "camera_name": "Hall South", "location": "Dining hall south"},
    {"camera_id": "ANN-EXIT-01", "camera_name": "Exit", "location": "Main exit"},
]

HALL_ID = "MAIN"
SEAT_CAMERA = "ANN-HALL-01"


def _polygon(column_index: int, row_index: int) -> list[dict[str, int]]:
    """Placeholder rectangle for a seat in a simple grid."""
    width, height, margin = 160, 120, 40
    x = margin + column_index * (width + margin)
    y = margin + row_index * (height + margin)
    return [
        {"x": x, "y": y},
        {"x": x + width, "y": y},
        {"x": x + width, "y": y + height},
        {"x": x, "y": y + height},
    ]


DEV_SEATS = [
    {
        "seat_id": f"S{index:02d}",
        "seat_label": f"Seat {index:02d}",
        "row_number": (index - 1) // 3 + 1,
        "column_number": (index - 1) % 3 + 1,
        "polygon_points": _polygon((index - 1) % 3, (index - 1) // 3),
    }
    for index in range(1, 7)  # S01..S06
]


def seed(reset: bool = False) -> None:
    settings = get_settings()
    if settings.app_env.lower() == "production":
        raise SystemExit("Refusing to seed: APP_ENV is production.")

    with SessionLocal() as session:
        if reset:
            for seat in session.scalars(select(Seat).where(Seat.hall_id == HALL_ID)):
                session.delete(seat)
            codes = [c["camera_id"] for c in DEV_CAMERAS]
            for camera in session.scalars(select(Camera).where(Camera.camera_id.in_(codes))):
                session.delete(camera)
            session.commit()
            print("Removed previously seeded cameras and seats.")

        cameras: dict[str, Camera] = {}
        for spec in DEV_CAMERAS:
            camera = session.scalar(select(Camera).where(Camera.camera_id == spec["camera_id"]))
            if camera is None:
                camera = Camera(**spec)
                session.add(camera)
                print(f"camera  {spec['camera_id']}  created")
            else:
                print(f"camera  {spec['camera_id']}  exists, skipped")
            cameras[spec["camera_id"]] = camera
        session.flush()

        hall_camera = cameras[SEAT_CAMERA]
        for spec in DEV_SEATS:
            existing = session.scalar(
                select(Seat).where(Seat.hall_id == HALL_ID, Seat.seat_id == spec["seat_id"])
            )
            if existing is None:
                session.add(Seat(hall_id=HALL_ID, camera_id=hall_camera.id, **spec))
                print(f"seat    {spec['seat_id']}  created")
            else:
                print(f"seat    {spec['seat_id']}  exists, skipped")

        session.commit()
    print("\nDevelopment seed complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed development cameras and seats.")
    parser.add_argument("--reset", action="store_true", help="delete seeded rows first")
    seed(reset=parser.parse_args().reset)
