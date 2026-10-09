"""ByteTrack tracking, isolated per camera.

Uses Ultralytics' own `BYTETracker` (the supported ByteTrack implementation) but
drives it directly rather than through `model.track()`, because `model.track()`
keeps its tracker inside the shared model object - which would couple every
camera's tracking state together. Here each camera owns its tracker instance.

Two properties of the upstream class are neutralised on purpose:

* Track IDs come from a process-wide counter (`BaseTrack._count`) that every
  `BYTETracker(...)` construction resets to zero. Nothing upstream therefore
  guarantees IDs stay unique for the lifetime of one camera's tracker once other
  trackers exist. Each tracker here gets its own STrack subclass with a private
  counter, so IDs are unique and increasing within (camera, tracker_session).
* Remaining class-level shared state is touched only while holding one lock.

A track_id is TEMPORARY. It is not a visitor and not a staff member.
"""

from __future__ import annotations

import itertools
import logging
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import numpy as np

from app.ai.types import (
    Detection,
    TrackedObject,
    TrackEvent,
    TrackEventType,
    TrackingResult,
    TrackState,
)

logger = logging.getLogger("temple_annadhanam.ai")

# Guards construction and update of BYTETracker instances (class-level state).
_BYTETRACK_LOCK = threading.Lock()


@dataclass(frozen=True)
class TrackerSettings:
    high_confidence: float      # detections >= this can start / continue tracks
    low_confidence: float       # detections between low and high only sustain tracks
    max_lost_frames: int        # frames a track survives without a match
    match_threshold: float = 0.8


class _TrackerInput:
    """Minimal 'results-like' object BYTETracker.update() accepts.

    It needs `conf`, `xywh`, `cls`, `len()` and boolean-mask indexing.
    """

    __slots__ = ("conf", "xywh", "cls")

    def __init__(self, conf: np.ndarray, xywh: np.ndarray, cls: np.ndarray) -> None:
        self.conf, self.xywh, self.cls = conf, xywh, cls

    def __len__(self) -> int:
        return len(self.conf)

    def __getitem__(self, index: Any) -> "_TrackerInput":
        return _TrackerInput(self.conf[index], self.xywh[index], self.cls[index])


def _to_tracker_input(detections: Sequence[Detection]) -> _TrackerInput:
    if not detections:
        return _TrackerInput(
            np.zeros((0,), np.float32), np.zeros((0, 4), np.float32), np.zeros((0,), np.float32)
        )
    conf = np.array([d.confidence for d in detections], dtype=np.float32)
    xywh = np.array(
        [[d.center_x, d.center_y, d.width, d.height] for d in detections], dtype=np.float32
    )
    cls = np.array([d.class_id for d in detections], dtype=np.float32)
    return _TrackerInput(conf, xywh, cls)


@dataclass
class _Record:
    """Bookkeeping for one live track."""

    class_id: int
    confidence: float
    bbox: tuple[float, float, float, float]
    first_seen: datetime
    last_seen: datetime
    hits: int
    state: TrackState


class ByteTrackTracker:
    """One camera's tracker. Not thread-safe: drive it from a single thread."""

    def __init__(
        self,
        camera_id: str,
        settings: TrackerSettings,
        class_names: Mapping[int, str] | None = None,
    ) -> None:
        self.camera_id = camera_id
        self._settings = settings
        self._class_names = dict(class_names or {})
        self._start_session()

    # ------------------------------------------------------------ lifecycle
    def _start_session(self) -> None:
        from ultralytics.trackers.byte_tracker import BYTETracker, STrack

        counter = itertools.count(1)

        class CameraSTrack(STrack):  # private ID space for this tracker only
            def next_id(self) -> int:  # type: ignore[override]
                return next(counter)

        args = SimpleNamespace(
            track_high_thresh=self._settings.high_confidence,
            track_low_thresh=self._settings.low_confidence,
            new_track_thresh=self._settings.high_confidence,
            track_buffer=self._settings.max_lost_frames,
            match_thresh=self._settings.match_threshold,
            fuse_score=True,
        )
        with _BYTETRACK_LOCK:
            tracker = BYTETracker(args)
        tracker.track_class = CameraSTrack

        self._tracker = tracker
        self._known: dict[int, _Record] = {}
        self.session_id = uuid.uuid4().hex[:12]

    @property
    def live_track_count(self) -> int:
        return len(self._known)

    def reset(self, timestamp: datetime) -> TrackingResult:
        """End the session: expire every live track and start a fresh tracker.

        Called when the stream reconnects, because tracks cannot be continued
        across a gap in the video. The returned result carries EXPIRED events so
        downstream state is closed cleanly.
        """
        expired: list[TrackedObject] = []
        events: list[TrackEvent] = []
        for track_id, record in self._known.items():
            obj = self._to_object(track_id, record, timestamp, TrackState.EXPIRED)
            expired.append(obj)
            events.append(TrackEvent(TrackEventType.EXPIRED, obj))
        old_session = self.session_id
        self._start_session()
        return TrackingResult(
            camera_id=self.camera_id,
            tracker_session=old_session,
            timestamp=timestamp,
            tracks=(),
            lost=(),
            expired=tuple(expired),
            events=tuple(events),
            session_ended=True,
        )

    # --------------------------------------------------------------- update
    def update(self, detections: Sequence[Detection], timestamp: datetime) -> TrackingResult:
        """Feed one frame's detections; get back tracks and lifecycle events.

        Call it every processed frame, even with no detections, so lost tracks
        age and eventually expire.
        """
        with _BYTETRACK_LOCK:
            output = self._tracker.update(_to_tracker_input(detections))
            lost_now = {int(t.track_id): t for t in self._tracker.lost_stracks}

        visible: list[TrackedObject] = []
        events: list[TrackEvent] = []
        seen: set[int] = set()

        rows = np.asarray(output).reshape(-1, 8) if np.size(output) else np.zeros((0, 8))
        for x1, y1, x2, y2, raw_id, score, class_value, _idx in rows:
            track_id = int(raw_id)
            seen.add(track_id)
            record = self._known.get(track_id)
            if record is None:
                record = _Record(
                    class_id=int(class_value),
                    confidence=float(score),
                    bbox=(float(x1), float(y1), float(x2), float(y2)),
                    first_seen=timestamp,
                    last_seen=timestamp,
                    hits=0,
                    state=TrackState.NEW,
                )
                self._known[track_id] = record
                state = TrackState.NEW
            else:
                state = TrackState.TRACKED
            record.hits += 1
            record.confidence = float(score)
            record.bbox = (float(x1), float(y1), float(x2), float(y2))
            record.class_id = int(class_value)
            record.last_seen = timestamp
            record.state = state
            obj = self._to_object(track_id, record, timestamp, state)
            visible.append(obj)
            if state is TrackState.NEW:
                events.append(TrackEvent(TrackEventType.NEW, obj))

        lost: list[TrackedObject] = []
        expired: list[TrackedObject] = []
        for track_id, record in list(self._known.items()):
            if track_id in seen:
                continue
            if track_id in lost_now:
                predicted = lost_now[track_id].xyxy
                record.bbox = tuple(float(v) for v in predicted)
                obj = self._to_object(track_id, record, timestamp, TrackState.LOST)
                if record.state is not TrackState.LOST:
                    record.state = TrackState.LOST
                    events.append(TrackEvent(TrackEventType.LOST, obj))
                lost.append(obj)
            else:
                obj = self._to_object(track_id, record, timestamp, TrackState.EXPIRED)
                expired.append(obj)
                events.append(TrackEvent(TrackEventType.EXPIRED, obj))
                del self._known[track_id]

        return TrackingResult(
            camera_id=self.camera_id,
            tracker_session=self.session_id,
            timestamp=timestamp,
            tracks=tuple(visible),
            lost=tuple(lost),
            expired=tuple(expired),
            events=tuple(events),
        )

    # -------------------------------------------------------------- helpers
    def _to_object(
        self, track_id: int, record: _Record, timestamp: datetime, state: TrackState
    ) -> TrackedObject:
        x1, y1, x2, y2 = record.bbox
        return TrackedObject(
            camera_id=self.camera_id,
            tracker_session=self.session_id,
            track_id=track_id,
            class_id=record.class_id,
            class_name=self._class_names.get(record.class_id, str(record.class_id)),
            confidence=record.confidence,
            x1=x1, y1=y1, x2=x2, y2=y2,
            timestamp=timestamp,
            state=state,
            hits=record.hits,
            first_seen=record.first_seen,
            last_seen=record.last_seen,
        )
