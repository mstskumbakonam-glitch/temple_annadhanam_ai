"""Event/state processors: turn pipeline output into events worth persisting.

A processor receives a PipelineResult and returns records (or any objects the
sink understands). The pipeline runs a list of them, so Phase 6 can add seat
occupancy, entry/exit lines and face matching as further processors without
changing the pipeline or the worker loop.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.ai.pipeline_types import PipelineResult
from app.models.enums import VisitorEventType
from app.services.runtime_events import TrackEventRecord


class FrameProcessor(Protocol):
    def process(self, result: PipelineResult) -> list[Any]:
        """Return the records to persist for this frame (usually none)."""


class TrackLifecycleProcessor:
    """Persists anonymous track lifecycle events, and only once per track.

    * DETECTED - written once, when a track has been matched in `min_hits`
      frames. That filters out one-frame ghosts a detector produces.
    * LOST     - written once, when a track that had a DETECTED row expires, or when
      its tracker session ends (stream lost, shutdown) so no DETECTED is left
      dangling. metadata.reason says which: "track_expired" or "tracker_session_ended".

    Both go to visitor_events with visitor_id NULL and the temporary ByteTrack
    id in tracking_id. No visitor identity is invented. Tracks that never reached
    `min_hits` leave no trace at all.
    """

    def __init__(self, camera_db_id: int, min_hits: int = 3, enabled: bool = True) -> None:
        if min_hits < 1:
            raise ValueError("min_hits must be at least 1")
        self._camera_db_id = camera_db_id
        self._min_hits = min_hits
        self._enabled = enabled
        # (tracker_session, track_id) of tracks that already have a DETECTED row
        self._persisted: set[tuple[str, int]] = set()

    @property
    def persisted_count(self) -> int:
        return len(self._persisted)

    def process(self, result: PipelineResult) -> list[Any]:
        tracking = result.tracking
        if not self._enabled or tracking is None:
            return []

        records: list[Any] = []
        for track in tracking.tracks:
            key = (track.tracker_session, track.track_id)
            if track.hits >= self._min_hits and key not in self._persisted:
                self._persisted.add(key)
                records.append(
                    TrackEventRecord(
                        camera_db_id=self._camera_db_id,
                        event_type=VisitorEventType.DETECTED,
                        tracking_id=track.track_id,
                        event_time=track.timestamp,
                        metadata={
                            "source": "bytetrack",
                            "anonymous": True,
                            "tracker_session": track.tracker_session,
                            "class": track.class_name,
                            "confidence": round(track.confidence, 3),
                            "bbox": [round(v, 1) for v in track.bbox],
                        },
                    )
                )

        reason = "tracker_session_ended" if tracking.session_ended else "track_expired"
        for track in tracking.expired:
            key = (track.tracker_session, track.track_id)
            if key in self._persisted:
                self._persisted.discard(key)
                records.append(
                    TrackEventRecord(
                        camera_db_id=self._camera_db_id,
                        event_type=VisitorEventType.LOST,
                        tracking_id=track.track_id,
                        event_time=track.timestamp,
                        metadata={
                            "source": "bytetrack",
                            "anonymous": True,
                            "tracker_session": track.tracker_session,
                            "reason": reason,
                            "hits": track.hits,
                            "lifetime_seconds": round(track.age_seconds, 2),
                        },
                    )
                )
        return records
