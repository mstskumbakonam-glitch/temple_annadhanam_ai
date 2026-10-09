"""Sliding-window event-rate meter (frames per second)."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class RateMeter:
    """Measures how often something happens, over the most recent `window` events.

    Not thread-safe by design: each meter belongs to a single worker thread, and
    readers get the value through the runtime-state snapshot instead.
    """

    def __init__(
        self,
        window: int = 20,
        stale_after: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if window < 2:
            raise ValueError("window must be at least 2")
        self._stamps: deque[float] = deque(maxlen=window)
        self._stale_after = stale_after
        self._clock = clock

    def tick(self) -> None:
        self._stamps.append(self._clock())

    @property
    def count(self) -> int:
        """Number of samples currently in the window."""
        return len(self._stamps)

    def rate(self) -> float:
        """Events per second, or 0.0 when there is too little or only stale data."""
        if len(self._stamps) < 2:
            return 0.0
        now = self._clock()
        if now - self._stamps[-1] > self._stale_after:
            return 0.0
        elapsed = self._stamps[-1] - self._stamps[0]
        if elapsed <= 0:
            return 0.0
        return (len(self._stamps) - 1) / elapsed

    def reset(self) -> None:
        self._stamps.clear()
