"""Reconnect back-off policy."""

from __future__ import annotations

import random
from collections.abc import Callable


class ReconnectPolicy:
    """Exponential back-off: delay, delay*factor, ... capped at max_delay.

    A failed camera retries quickly at first (a brief network blip) and then
    settles to a slow poll so a dead camera does not spin the CPU or flood logs.
    `jitter` spreads retries so many cameras do not reconnect in lockstep after
    a switch reboot.
    """

    def __init__(
        self,
        initial_delay: float = 2.0,
        max_delay: float = 60.0,
        factor: float = 2.0,
        jitter: float = 0.1,
        rng: Callable[[], float] = random.random,
    ) -> None:
        if initial_delay <= 0 or max_delay < initial_delay:
            raise ValueError("require 0 < initial_delay <= max_delay")
        if factor < 1.0:
            raise ValueError("factor must be >= 1")
        if not 0.0 <= jitter < 1.0:
            raise ValueError("jitter must be in [0, 1)")
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.factor = factor
        self.jitter = jitter
        self._rng = rng
        self._attempts = 0

    @property
    def attempts(self) -> int:
        """Consecutive failed attempts since the last reset()."""
        return self._attempts

    def next_delay(self) -> float:
        """Delay before the next attempt; advances the back-off."""
        base = min(self.max_delay, self.initial_delay * (self.factor ** self._attempts))
        self._attempts += 1
        if self.jitter:
            base *= 1.0 + self.jitter * (2.0 * self._rng() - 1.0)
        return max(0.0, min(base, self.max_delay))

    def reset(self) -> None:
        """Call after a successful connection."""
        self._attempts = 0
