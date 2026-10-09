"""Typed structures shared by the detection, tracking and pipeline layers.

IDENTITY RULES (do not blur these):

* `track_id`      - a TEMPORARY id assigned by ByteTrack. It is only meaningful
                    inside one (camera, tracker_session). It restarts after a
                    reconnect and is NOT a visitor.
* `visitor_code`  - permanent PostgreSQL identity, VIS-000001. Never produced here.
* `staff_code`    - permanent PostgreSQL identity, STAFF-001. Never produced here.

Nothing in this module carries a face, an embedding, or a permanent identity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class MalformedDetectionError(ValueError):
    """A detection failed validation and must be discarded."""


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Detection:
    """One object found in one frame. Pixel coordinates, origin top-left."""

    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    frame_timestamp: datetime

    # Derived geometry, computed once at construction.
    center_x: float = field(init=False)
    center_y: float = field(init=False)
    bottom_center_x: float = field(init=False)
    bottom_center_y: float = field(init=False)

    def __post_init__(self) -> None:
        values = (self.confidence, self.x1, self.y1, self.x2, self.y2)
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            raise MalformedDetectionError("non-finite or non-numeric detection value")
        if not 0.0 <= self.confidence <= 1.0:
            raise MalformedDetectionError(f"confidence {self.confidence} outside [0, 1]")
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise MalformedDetectionError("bounding box has no area (x2<=x1 or y2<=y1)")
        if self.class_id < 0:
            raise MalformedDetectionError("negative class_id")
        if self.frame_timestamp.tzinfo is None:
            raise MalformedDetectionError("frame_timestamp must be timezone-aware (UTC)")

        # frozen dataclass: assign derived fields through object.__setattr__
        object.__setattr__(self, "center_x", (self.x1 + self.x2) / 2.0)
        object.__setattr__(self, "center_y", (self.y1 + self.y2) / 2.0)
        # A seat is judged by where a person's feet/hips are, i.e. the bottom edge.
        object.__setattr__(self, "bottom_center_x", (self.x1 + self.x2) / 2.0)
        object.__setattr__(self, "bottom_center_y", self.y2)

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)

    @classmethod
    def from_xyxy(
        cls,
        xyxy: Any,
        confidence: float,
        class_id: int,
        class_name: str,
        frame_timestamp: datetime,
        frame_shape: tuple[int, ...] | None = None,
    ) -> "Detection":
        """Build a Detection from a raw [x1, y1, x2, y2] box, clamped to the frame.

        Raises MalformedDetectionError for anything unusable.
        """
        try:
            x1, y1, x2, y2 = (float(v) for v in xyxy)
            conf = float(confidence)
            cid = int(class_id)
        except (TypeError, ValueError, OverflowError) as exc:
            raise MalformedDetectionError(f"unparseable detection: {exc}") from exc

        if frame_shape is not None and len(frame_shape) >= 2:
            height, width = frame_shape[0], frame_shape[1]
            if all(math.isfinite(v) for v in (x1, y1, x2, y2)):
                x1, x2 = max(0.0, min(x1, width)), max(0.0, min(x2, width))
                y1, y2 = max(0.0, min(y1, height)), max(0.0, min(y2, height))

        return cls(
            class_id=cid,
            class_name=class_name,
            confidence=conf,
            x1=x1, y1=y1, x2=x2, y2=y2,
            frame_timestamp=frame_timestamp,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": round(self.confidence, 4),
            "bbox": [round(v, 1) for v in self.bbox],
            "center": [round(self.center_x, 1), round(self.center_y, 1)],
            "bottom_center": [round(self.bottom_center_x, 1), round(self.bottom_center_y, 1)],
        }


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------
class TrackState(StrEnum):
    NEW = "NEW"          # first frame this track was seen
    TRACKED = "TRACKED"  # matched again this frame
    LOST = "LOST"        # not matched this frame, still inside the lost buffer
    EXPIRED = "EXPIRED"  # lost for too long; the id will never be seen again


class TrackEventType(StrEnum):
    NEW = "NEW"
    LOST = "LOST"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class TrackedObject:
    """A detection that carries a temporary ByteTrack identity.

    `track_id` is scoped to (`camera_id`, `tracker_session`). It is not a
    visitor and must never be stored as one.
    """

    camera_id: str          # camera display code
    tracker_session: str    # changes whenever the tracker is rebuilt (e.g. reconnect)
    track_id: int           # TEMPORARY, per camera+session
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    timestamp: datetime
    state: TrackState
    hits: int               # frames in which the track was matched
    first_seen: datetime
    last_seen: datetime

    @property
    def bottom_center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, self.y2)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def track_key(self) -> str:
        """Globally unambiguous key: camera + tracker session + track id."""
        return f"{self.camera_id}:{self.tracker_session}:{self.track_id}"

    @property
    def age_seconds(self) -> float:
        return max(0.0, (self.last_seen - self.first_seen).total_seconds())


@dataclass(frozen=True, slots=True)
class TrackEvent:
    """A lifecycle transition of a track."""

    type: TrackEventType
    track: TrackedObject


@dataclass(frozen=True, slots=True)
class TrackingResult:
    """Everything the tracker knows after one update."""

    camera_id: str
    tracker_session: str
    timestamp: datetime
    tracks: tuple[TrackedObject, ...]    # visible now (NEW or TRACKED)
    lost: tuple[TrackedObject, ...]      # temporarily unmatched, still alive
    expired: tuple[TrackedObject, ...]   # removed this update
    events: tuple[TrackEvent, ...]       # NEW / LOST / EXPIRED transitions this update
    # True when the tracker session was closed (reconnect / shutdown) rather than
    # tracks expiring naturally. The EXPIRED events then mean "session ended".
    session_ended: bool = False

    @property
    def visible_count(self) -> int:
        return len(self.tracks)

    @property
    def active_count(self) -> int:
        """Alive tracks: visible plus temporarily lost."""
        return len(self.tracks) + len(self.lost)
