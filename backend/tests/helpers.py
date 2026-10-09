"""Deterministic fakes for Phase 5 tests.

Nothing here needs a camera, a GPU, network access or real model weights.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from app.ai.model_loader import ModelHandle
from app.camera.frame_buffer import FramePacket

T0 = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


def ts(seconds: float = 0.0) -> datetime:
    return T0 + timedelta(seconds=seconds)


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def person_box(cx: float, cy: float = 200.0, w: float = 60.0, h: float = 120.0):
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def frame(height: int = 300, width: int = 1280, value: int = 10) -> np.ndarray:
    return np.full((height, width, 3), value, dtype=np.uint8)


_DEFAULT_FRAME = object()


def packet(camera_id: str = "CAM-1", seq: int = 1, when: float = 0.0, img: Any = _DEFAULT_FRAME) -> FramePacket:
    return FramePacket(
        camera_id=camera_id,
        frame=frame() if img is _DEFAULT_FRAME else img,
        sequence=seq,
        timestamp=ts(when),
        monotonic=time.monotonic(),
    )


# ------------------------------------------------------------------ fake YOLO
class FakeBoxes:
    def __init__(self, rows: list[tuple]) -> None:
        arr = np.array(rows, dtype=np.float32).reshape(-1, 6) if rows else np.zeros((0, 6), np.float32)
        self.xyxy = arr[:, :4]
        self.conf = arr[:, 4]
        self.cls = arr[:, 5]

    def __len__(self) -> int:
        return len(self.conf)


class FakeResult:
    def __init__(self, rows: list[tuple], names: dict[int, str]) -> None:
        self.boxes = FakeBoxes(rows)
        self.names = names


class FakeTensor:
    """Mimics a torch tensor's .cpu().numpy() chain."""

    def __init__(self, array: Any) -> None:
        self._array = np.asarray(array, dtype=np.float32)

    def cpu(self) -> "FakeTensor":
        return self

    def numpy(self) -> np.ndarray:
        return self._array


class FakeYoloModel:
    """Scriptable stand-in for an Ultralytics YOLO model.

    `script(call_index, frame)` returns rows of (x1, y1, x2, y2, conf, class_id).
    The fake honours the `classes` filter the way the real model does, records
    every call, and can measure how many threads are inside predict() at once.
    """

    names = {0: "person", 1: "bicycle", 2: "car"}

    def __init__(
        self,
        script: Callable[[int, Any], list[tuple]] | None = None,
        *,
        delay: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self._script = script or (lambda i, f: [])
        self._delay = delay
        self._error = error
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._active = 0
        self.max_concurrent = 0

    def predict(self, frame: Any, **kwargs: Any) -> list[FakeResult]:
        with self._lock:
            index = len(self.calls)
            self.calls.append(kwargs)
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        try:
            if self._delay:
                time.sleep(self._delay)
            if self._error:
                raise self._error
            rows = list(self._script(index, frame))
            classes = kwargs.get("classes")
            if classes is not None:
                rows = [r for r in rows if int(r[5]) in classes]
            conf = kwargs.get("conf", 0.0)
            rows = [r for r in rows if r[4] >= conf]
            return [FakeResult(rows, self.names)]
        finally:
            with self._lock:
                self._active -= 1


def make_handle(model: Any | None = None, device: str = "cpu") -> ModelHandle:
    model = model or FakeYoloModel()
    return ModelHandle(model, path=Path("fake-model.pt"), device=device, names=model.names)


def walking_people(centres_by_frame: Callable[[int], list[float]], conf: float = 0.9):
    """Script: people at the x-positions returned for each call index."""
    def script(index: int, _frame: Any) -> list[tuple]:
        return [(*person_box(cx), conf, 0) for cx in centres_by_frame(index)]
    return script


# ---------------------------------------------------------------- fake stream
class SourceScript:
    """Controls how a family of scripted RTSP sources behaves.

    open_results     - outcome of each open() attempt in order (last one repeats)
    frames_per_conn  - frames delivered per successful connection before the
                       stream 'drops' (None = never drops)
    frame_delay      - seconds per frame, to simulate a camera's frame rate
    """

    def __init__(
        self,
        open_results: tuple[bool, ...] = (True,),
        frames_per_conn: int | None = None,
        frame_delay: float = 0.002,
        shape: tuple[int, int, int] = (300, 1280, 3),
        open_raises: type[Exception] | None = None,
    ) -> None:
        self.open_results = list(open_results)
        self.frames_per_conn = frames_per_conn
        self.frame_delay = frame_delay
        self.shape = shape
        self.open_raises = open_raises
        self._lock = threading.Lock()
        self.open_calls = 0
        self.releases = 0
        self.frames_served = 0
        self.urls_seen: list[str] = []

    def factory(self, url: str) -> "ScriptedSource":
        with self._lock:
            self.urls_seen.append(url)
        return ScriptedSource(self)

    def _next_open(self) -> bool:
        with self._lock:
            index = min(self.open_calls, len(self.open_results) - 1)
            self.open_calls += 1
            return self.open_results[index]


class ScriptedSource:
    def __init__(self, script: SourceScript) -> None:
        self._script = script
        self._opened = False
        self._served = 0

    def open(self, timeout: float) -> bool:
        if self._script.open_raises:
            raise self._script.open_raises("boom")
        self._opened = self._script._next_open()
        return self._opened

    def read(self):
        script = self._script
        if not self._opened or (
            script.frames_per_conn is not None and self._served >= script.frames_per_conn
        ):
            time.sleep(0.001)
            return False, None
        time.sleep(script.frame_delay)
        self._served += 1
        with script._lock:
            script.frames_served += 1
        return True, np.full(script.shape, self._served % 250, dtype=np.uint8)

    def release(self) -> None:
        with self._script._lock:
            self._script.releases += 1
        self._opened = False
