"""Thread-safe latest-frame buffer.

Capture must never be slowed by inference, and inference must never see a stale
backlog. The buffer therefore holds a fixed, tiny number of frames (one by
default): writing overwrites the oldest, reading returns the newest. There is no
queue that can grow, so a slow consumer costs dropped frames, not memory.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.utils.time import utc_now


@dataclass(frozen=True, slots=True)
class FramePacket:
    """One captured frame plus the metadata needed downstream."""

    camera_id: str          # camera display code, e.g. ANN-ENT-01
    frame: Any              # numpy ndarray (BGR); treated as read-only
    sequence: int           # 1, 2, 3... per buffer, strictly increasing
    timestamp: datetime     # UTC capture time
    monotonic: float        # monotonic capture time, for latency/staleness maths
    # Continuity epoch: changes when the video jumps (stream reconnected, demo
    # video looped). Frames from different epochs must never share a track.
    epoch: int = 0


class LatestFrameBuffer:
    """Bounded buffer that keeps only the newest frame(s) for one camera."""

    def __init__(self, camera_id: str, capacity: int = 1) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.camera_id = camera_id
        self._capacity = capacity
        self._frames: deque[FramePacket] = deque(maxlen=capacity)
        self._condition = threading.Condition()
        self._sequence = 0
        self._last_read_sequence = 0
        self._dropped = 0

    # ------------------------------------------------------------- properties
    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._condition:
            return len(self._frames)

    @property
    def sequence(self) -> int:
        """Sequence number of the most recently written frame (0 = none yet)."""
        with self._condition:
            return self._sequence

    @property
    def dropped(self) -> int:
        """Frames overwritten before any consumer read them."""
        with self._condition:
            return self._dropped

    # ------------------------------------------------------------------ write
    def put(self, frame: Any, timestamp: datetime | None = None, epoch: int = 0) -> FramePacket:
        """Store a new frame, discarding the oldest when full."""
        try:
            frame.flags.writeable = False  # shared between threads: forbid mutation
        except AttributeError:
            pass

        with self._condition:
            self._sequence += 1
            if len(self._frames) == self._capacity:
                evicted = self._frames[0]
                if evicted.sequence > self._last_read_sequence:
                    self._dropped += 1
            packet = FramePacket(
                camera_id=self.camera_id,
                frame=frame,
                sequence=self._sequence,
                timestamp=timestamp or utc_now(),
                monotonic=time.monotonic(),
                epoch=epoch,
            )
            self._frames.append(packet)
            self._condition.notify_all()
            return packet

    # ------------------------------------------------------------------- read
    def get_latest(self) -> FramePacket | None:
        """Newest frame, or None when nothing has been captured yet."""
        with self._condition:
            if not self._frames:
                return None
            packet = self._frames[-1]
            self._last_read_sequence = max(self._last_read_sequence, packet.sequence)
            return packet

    def get_newer_than(self, sequence: int) -> FramePacket | None:
        """Newest frame only if it is newer than `sequence`, else None."""
        with self._condition:
            if not self._frames or self._frames[-1].sequence <= sequence:
                return None
            packet = self._frames[-1]
            self._last_read_sequence = max(self._last_read_sequence, packet.sequence)
            return packet

    def wait_for_frame(self, after_sequence: int, timeout: float) -> FramePacket | None:
        """Block until a frame newer than `after_sequence` exists, or time out."""
        with self._condition:
            self._condition.wait_for(
                lambda: bool(self._frames) and self._frames[-1].sequence > after_sequence,
                timeout=timeout,
            )
            if self._frames and self._frames[-1].sequence > after_sequence:
                packet = self._frames[-1]
                self._last_read_sequence = max(self._last_read_sequence, packet.sequence)
                return packet
            return None

    def clear(self) -> None:
        """Drop buffered frames (e.g. on disconnect) without resetting sequence numbers."""
        with self._condition:
            self._frames.clear()
            self._condition.notify_all()
