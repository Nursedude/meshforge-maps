"""
MeshForge Maps - Reconnect Strategy

Exponential backoff with jitter for resilient reconnection.
Prevents thundering herd on broker recovery and provides
configurable retry policies per data source type.

Inspired by MeshForge core gateway/reconnect.py
"""

import logging
import random
import time
from typing import Optional

logger = logging.getLogger(__name__)


class ReconnectStrategy:
    """Exponential backoff with jitter for reconnection attempts.

    Computes successive delays using: delay = base * (multiplier ^ attempt) + jitter
    Jitter is randomized as uniform(0, delay * jitter_factor) to decorrelate
    multiple clients reconnecting simultaneously.
    """

    def __init__(
        self,
        base_delay: float = 2.0,
        max_delay: float = 60.0,
        multiplier: float = 2.0,
        jitter_factor: float = 0.25,
        max_retries: Optional[int] = None,
    ):
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._multiplier = multiplier
        self._jitter_factor = jitter_factor
        self._max_retries = max_retries

        self._attempt: int = 0
        self._total_attempts: int = 0
        self._last_attempt_time: float = 0

    @property
    def attempt(self) -> int:
        """Current attempt number (0-indexed)."""
        return self._attempt

    @property
    def total_attempts(self) -> int:
        """Total attempts across all reset cycles."""
        return self._total_attempts

    def next_delay(self) -> float:
        """Calculate the next backoff delay with jitter.

        Returns the delay in seconds. Increments the attempt counter.
        """
        delay = self._base_delay * (self._multiplier ** self._attempt)
        delay = min(delay, self._max_delay)

        jitter = random.uniform(0, delay * self._jitter_factor)
        delay += jitter

        self._attempt += 1
        self._total_attempts += 1
        self._last_attempt_time = time.time()

        return delay

    def should_retry(self) -> bool:
        """Check if another retry is allowed.

        Returns True if max_retries is None (unlimited) or
        the attempt count is below the limit.
        """
        if self._max_retries is None:
            return True
        return self._attempt < self._max_retries

    def reset(self) -> None:
        """Reset the attempt counter after a successful connection."""
        self._attempt = 0

    def wait(self) -> float:
        """Calculate delay and sleep for that duration.

        Returns the actual delay waited (seconds).
        """
        delay = self.next_delay()
        time.sleep(delay)
        return delay

    @classmethod
    def for_mqtt(cls) -> "ReconnectStrategy":
        """Factory: strategy tuned for MQTT broker reconnection.

        Starts at 2s, maxes at 120s, unlimited retries (persistent connection).
        """
        return cls(
            base_delay=2.0,
            max_delay=120.0,
            multiplier=2.0,
            jitter_factor=0.25,
            max_retries=None,
        )

    @classmethod
    def for_collector(cls) -> "ReconnectStrategy":
        """Factory: strategy tuned for HTTP collector retries.

        Starts at 1s, maxes at 10s, limited to 3 retries before cache fallback.
        """
        return cls(
            base_delay=1.0,
            max_delay=10.0,
            multiplier=2.0,
            jitter_factor=0.15,
            max_retries=3,
        )
