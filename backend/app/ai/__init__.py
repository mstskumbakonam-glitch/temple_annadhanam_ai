"""AI pipeline: YOLO person detection and ByteTrack tracking.

Layers (each independent):
    model_loader  -> one shared, lock-protected YOLO model per process
    detector      -> frame -> validated Detection objects
    tracker       -> Detection -> temporary per-camera track ids
    processor     -> tracker output -> events worth persisting
    pipeline      -> wires the layers for one camera

Nothing in this package identifies a person. Track ids are temporary; visitor
and staff identities belong to later phases.
"""

from app.ai.types import (
    Detection,
    MalformedDetectionError,
    TrackedObject,
    TrackEvent,
    TrackEventType,
    TrackingResult,
    TrackState,
)

__all__ = [
    "Detection",
    "MalformedDetectionError",
    "TrackedObject",
    "TrackEvent",
    "TrackEventType",
    "TrackingResult",
    "TrackState",
]
