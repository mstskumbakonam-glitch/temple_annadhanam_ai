"""Recorded-video frame source for DEMO mode.

A camera whose URL is `demo://<file name>` plays that file from DEMO_VIDEO_DIR in
a loop, at the file's own frame rate, through the SAME capture -> YOLO ->
ByteTrack -> analytics pipeline a live camera uses. The detections are therefore
real model output, but on RECORDED footage: the API marks such cameras
`source_kind="recorded"` and the dashboard labels them, so they are never
mistaken for a live camera.

Safety: only a bare file name is accepted (no directories, no '..'), it must
resolve inside DEMO_VIDEO_DIR, and demo sources work only when DEMO_MODE=true.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("temple_annadhanam.camera")

DEMO_SCHEME = "demo://"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"}


class DemoSourceError(ValueError):
    """The demo URL is malformed or points outside the demo directory."""


def is_demo_url(url: str | None) -> bool:
    return bool(url) and url.strip().lower().startswith(DEMO_SCHEME)


def demo_file_name(url: str) -> str:
    """Validate `demo://name.mp4` and return `name.mp4`."""
    if not is_demo_url(url):
        raise DemoSourceError("not a demo:// URL")
    name = url.strip()[len(DEMO_SCHEME):]
    if not _SAFE_NAME.fullmatch(name) or ".." in name:
        raise DemoSourceError("demo:// takes a plain file name, e.g. demo://queue.mp4")
    if Path(name).suffix.lower() not in VIDEO_SUFFIXES:
        raise DemoSourceError(f"unsupported video type; use one of {sorted(VIDEO_SUFFIXES)}")
    return name


def resolve_demo_path(url: str, demo_dir: Path) -> Path:
    name = demo_file_name(url)
    base = demo_dir.resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise DemoSourceError("demo file must be directly inside DEMO_VIDEO_DIR")
    return path


class VideoFileFrameSource:
    """Plays a video file in a loop at its native frame rate (FrameSource protocol)."""

    def __init__(
        self,
        path: Path,
        *,
        loop: bool = True,
        fallback_fps: float = 25.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._path = path
        self._loop = loop
        self._fallback_fps = fallback_fps
        self._clock = clock
        self._sleep = sleep
        self._capture: Any = None
        self._interval = 1.0 / fallback_fps
        self._next_due = 0.0
        self.loops = 0

    def open(self, timeout: float) -> bool:
        import cv2

        if not self._path.is_file():
            logger.warning("[CAMERA] demo video missing: %s", self._path.name)
            return False
        capture = cv2.VideoCapture(str(self._path))
        if not capture.isOpened():
            capture.release()
            return False
        fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
        self._interval = 1.0 / (fps if 1.0 <= fps <= 120.0 else self._fallback_fps)
        self._capture = capture
        self._next_due = self._clock()
        return True

    def read(self) -> tuple[bool, Any]:
        if self._capture is None:
            return False, None
        # Pace to the file's frame rate: a file can be decoded far faster than
        # real time, which would make every timing-based metric meaningless.
        wait = self._next_due - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._next_due = max(self._next_due + self._interval, self._clock() - self._interval)

        ok, frame = self._capture.read()
        if not ok and self._loop:
            import cv2

            self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self.loops += 1
            ok, frame = self._capture.read()
        return bool(ok), frame

    def release(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass


class _RefusingSource:
    """Stands in for a source that must not open (demo disabled / bad URL)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def open(self, timeout: float) -> bool:
        return False

    def read(self) -> tuple[bool, Any]:
        return False, None

    def release(self) -> None:
        pass
