"""Runtime state of the camera/AI pipeline.

This is the internal interface Phase 6 and the API read. It holds live values
only - nothing here is written to the database just to have something to show.

Counts are `None` (unknown), never a made-up 0, whenever the camera is not
connected or AI is not running.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, fields, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.ai.types import TrackedObject
from app.utils.time import utc_now


class Connection(StrEnum):
    NOT_RUNNING = "not_running"   # no worker exists for this camera
    CONNECTING = "connecting"
    ONLINE = "online"
    OFFLINE = "offline"           # was connected, stream lost
    ERROR = "error"               # could not connect
    STOPPED = "stopped"           # worker was stopped cleanly


@dataclass(frozen=True)
class CameraRuntimeSnapshot:
    """Immutable view of one camera's runtime state."""

    camera_id: str
    connection_state: Connection = Connection.NOT_RUNNING
    connected: bool = False
    ai_running: bool = False
    model_loaded: bool = False
    person_count: int | None = None       # confirmed tracks visible in the last frame
    active_tracks: int | None = None      # visible + temporarily lost tracks
    processing_fps: float | None = None
    capture_fps: float | None = None
    last_frame_timestamp: datetime | None = None
    last_detection_timestamp: datetime | None = None
    frames_captured: int = 0
    frames_processed: int = 0
    frames_dropped: int = 0
    reconnect_count: int = 0
    rejected_detections: int = 0
    last_error: str | None = None
    started_at: datetime | None = None
    tracker_session: str | None = None
    source_kind: str = "live"             # "live" camera or "recorded" demo video
    inference_ms: float | None = None     # model time for the last processed frame
    analytics: Any = None                 # app.ai.analytics.AnalyticsSnapshot | None


_FIELD_NAMES = {f.name for f in fields(CameraRuntimeSnapshot)}


class CameraRuntime:
    """Mutable, lock-protected state for one camera. Written by its worker."""

    def __init__(self, camera_id: str) -> None:
        self.camera_id = camera_id
        self._lock = threading.Lock()
        self._snapshot = CameraRuntimeSnapshot(camera_id=camera_id)
        self._tracks: tuple[TrackedObject, ...] = ()
        self._preview: tuple[Any, tuple[TrackedObject, ...], datetime] | None = None

    def update(self, **changes: Any) -> None:
        unknown = set(changes) - _FIELD_NAMES
        if unknown:
            raise AttributeError(f"unknown runtime fields: {sorted(unknown)}")
        with self._lock:
            self._snapshot = replace(self._snapshot, **changes)

    def increment(self, **deltas: int) -> None:
        with self._lock:
            current = {name: getattr(self._snapshot, name) + delta for name, delta in deltas.items()}
            self._snapshot = replace(self._snapshot, **current)

    def snapshot(self) -> CameraRuntimeSnapshot:
        with self._lock:
            return self._snapshot

    # -- latest tracks (for Phase 6: seat occupancy, line crossing, ...) -------
    def set_tracks(self, tracks: tuple[TrackedObject, ...]) -> None:
        with self._lock:
            self._tracks = tracks

    def latest_tracks(self) -> tuple[TrackedObject, ...]:
        with self._lock:
            return self._tracks

    # -- latest processed frame, kept only when PREVIEW_ENABLED --------------
    def set_preview(self, frame: Any, tracks: tuple[TrackedObject, ...], when: datetime) -> None:
        with self._lock:
            self._preview = (frame, tracks, when)

    def clear_preview(self) -> None:
        with self._lock:
            self._preview = None

    def preview(self) -> tuple[Any, tuple[TrackedObject, ...], datetime] | None:
        with self._lock:
            return self._preview


class RuntimeRegistry:
    """All cameras' runtime state, keyed by camera display code."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runtimes: dict[str, CameraRuntime] = {}

    def register(self, camera_id: str) -> CameraRuntime:
        """Create (or replace) the runtime for a camera whose worker is starting."""
        with self._lock:
            runtime = CameraRuntime(camera_id)
            self._runtimes[camera_id] = runtime
            return runtime

    def get(self, camera_id: str) -> CameraRuntime | None:
        with self._lock:
            return self._runtimes.get(camera_id)

    def snapshot(self, camera_id: str) -> CameraRuntimeSnapshot:
        """Snapshot for a camera; a NOT_RUNNING placeholder if it has no worker."""
        runtime = self.get(camera_id)
        return runtime.snapshot() if runtime else CameraRuntimeSnapshot(camera_id=camera_id)

    def snapshots(self) -> list[CameraRuntimeSnapshot]:
        with self._lock:
            runtimes = list(self._runtimes.values())
        return [r.snapshot() for r in runtimes]

    def remove(self, camera_id: str) -> None:
        with self._lock:
            self._runtimes.pop(camera_id, None)

    def clear(self) -> None:
        with self._lock:
            self._runtimes.clear()


# ---------------------------------------------------------------------------
# Manager-level status
# ---------------------------------------------------------------------------
class ManagerState(StrEnum):
    DISABLED = "disabled"                  # AI_AUTOSTART is off; nothing was started
    STARTING = "starting"
    RUNNING = "running"
    MODEL_UNAVAILABLE = "model_unavailable"
    NOT_OWNER = "not_owner"                # another process already runs the pipeline
    STOPPED = "stopped"


@dataclass(frozen=True)
class ManagerStatus:
    state: ManagerState = ManagerState.DISABLED
    detail: str | None = None
    model_loaded: bool = False
    model_name: str | None = None          # file name only, never a full path
    device: str | None = None
    updated_at: datetime | None = None


class ManagerStatusHolder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = ManagerStatus()

    def set(self, state: ManagerState, detail: str | None = None, **model: Any) -> None:
        with self._lock:
            keep = {
                "model_loaded": self._status.model_loaded,
                "model_name": self._status.model_name,
                "device": self._status.device,
            }
            keep.update(model)
            self._status = ManagerStatus(
                state=state, detail=detail, updated_at=utc_now(), **keep
            )

    def get(self) -> ManagerStatus:
        with self._lock:
            return self._status


# Process-wide singletons read by the API. They are always safe to read: with the
# pipeline off they simply describe an idle system.
runtime_registry = RuntimeRegistry()
manager_status = ManagerStatusHolder()
