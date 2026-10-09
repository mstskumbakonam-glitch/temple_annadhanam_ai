#!/usr/bin/env python3
"""DEMO ONLY: register recorded-video cameras with counting lines and zones.

Each demo camera plays a video file from DEMO_VIDEO_DIR (default backend/demo_videos)
through the real YOLO + ByteTrack + analytics pipeline. The detections are real
model output on RECORDED footage; the API and dashboard label these cameras
"recorded", never "live".

Usage (from backend/, virtualenv active, DEMO_MODE=true in .env):
    python ../scripts/seed_demo.py --video crowd.mp4            # one camera, default layout
    python ../scripts/seed_demo.py --video gate.mp4 --video hall.mp4
    python ../scripts/seed_demo.py --reset                      # remove demo cameras

Use only footage you have permission to use. The default line/zone layout is a
starting point: draw your own to match the scene (coordinates are 0..1 fractions
of the frame, origin top-left).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.ai.analytics_config import AnalyticsConfig  # noqa: E402
from app.camera.video_file import VIDEO_SUFFIXES  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Camera  # noqa: E402

PREFIX = "DEMO-"

# A horizontal gate line across the middle (people walking DOWN the image count as
# entries), a queue zone on the left and a general area zone on the right.
DEFAULT_LAYOUT = {
    "tuning": {"process_fps": 5},
    "lines": [{"id": "GATE", "name": "Gate line", "start": [0.05, 0.55], "end": [0.95, 0.55]}],
    "zones": [
        {"id": "QUEUE", "name": "Food queue", "kind": "queue",
         "polygon": [[0.0, 0.0], [0.45, 0.0], [0.45, 1.0], [0.0, 1.0]],
         "queue_alert_length": 12,
         "thresholds": {"medium": 8, "high": 12, "critical": 18}},
        {"id": "HALL", "name": "Hall area", "kind": "area",
         "polygon": [[0.55, 0.0], [1.0, 0.0], [1.0, 1.0], [0.55, 1.0]],
         "thresholds": {"medium": 6, "high": 10, "critical": 15}},
    ],
    "alerts": {"raise_after_seconds": 5, "clear_after_seconds": 15, "min_level": "HIGH"},
}


def camera_code(video: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "-", Path(video).stem).strip("-").upper()[:40] or "VIDEO"
    return f"{PREFIX}{stem}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", action="append", default=[], help="file name inside DEMO_VIDEO_DIR")
    parser.add_argument("--reset", action="store_true", help="delete all DEMO-* cameras first")
    args = parser.parse_args()

    settings = get_settings()
    if settings.is_production:
        raise SystemExit("Refusing to seed demo cameras: APP_ENV is production.")
    if not settings.demo_mode and args.video:
        print("WARNING: DEMO_MODE is false; demo cameras will be registered but will not play "
              "until you set DEMO_MODE=true and restart the backend.")
    layout = AnalyticsConfig.model_validate(DEFAULT_LAYOUT).model_dump(mode="json")

    with SessionLocal() as session:
        if args.reset:
            for camera in session.scalars(select(Camera).where(Camera.camera_id.like(f"{PREFIX}%"))):
                session.delete(camera)
            session.commit()
            print("Removed demo cameras (and their analytics history).")

        for video in args.video:
            if Path(video).name != video or Path(video).suffix.lower() not in VIDEO_SUFFIXES:
                raise SystemExit(f"--video takes a plain file name with one of {sorted(VIDEO_SUFFIXES)}")
            path = settings.demo_video_path / video
            if not path.is_file():
                print(f"WARNING: {path} does not exist yet; copy the video there.")
            code = camera_code(video)
            camera = session.scalar(select(Camera).where(Camera.camera_id == code))
            if camera is None:
                camera = Camera(camera_id=code, camera_name=f"{Path(video).stem} (recorded)",
                                location="Demo - recorded video", enabled=True)
                session.add(camera)
                print(f"camera  {code}  created")
            else:
                print(f"camera  {code}  exists, updated")
            camera.rtsp_url = f"demo://{video}"
            camera.analytics_config = layout
        session.commit()
    print("\nDone. Start the backend with AI_AUTOSTART=true and DEMO_MODE=true.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
