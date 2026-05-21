"""Per-IP token-bucket rate limiter for the MeshForge Maps HTTP server.

Stdlib-only. Used to protect public-facing API endpoints from unauthenticated
scraping or denial-of-service. Buckets are keyed by client IP and pruned when
they've been idle long enough that a full refill is guaranteed.

Usage:
    limiter = RateLimiter(requests_per_minute=60)
    allowed, retry_after = limiter.allow("203.0.113.42")
    if not allowed:
        return 429, {"Retry-After": str(retry_after)}
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Tuple


class RateLimiter:
    """Token-bucket rate limiter keyed by client IP.

    A request consumes one token. Tokens refill at ``requests_per_minute / 60``
    per second up to a cap of ``requests_per_minute``. When the bucket is
    empty, ``allow()`` returns ``(False, retry_after_seconds)`` so the caller
    can emit a ``Retry-After`` header.

    Thread-safe: a single lock guards the bucket dict and per-bucket state.
    The bucket is small (two floats per IP) and we prune idle entries on a
    timer to keep memory bounded under hostile traffic.
    """

    # Prune buckets that haven't been touched in this many seconds. Set well
    # above the refill window so a long-idle client doesn't lose pre-earned
    # capacity within normal use.
    _PRUNE_IDLE_SECONDS = 600

    def __init__(self, requests_per_minute: int = 60) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self._capacity = float(requests_per_minute)
        self._refill_per_second = self._capacity / 60.0
        self._buckets: Dict[str, Tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._last_prune = time.monotonic()

    def allow(self, ip: str) -> Tuple[bool, int]:
        """Consume one token for ``ip``. Returns (allowed, retry_after_seconds).

        When allowed, ``retry_after_seconds`` is 0. When denied, it's the
        ceiling of the seconds until at least one token is available again.
        """
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(ip, (self._capacity, now))
            tokens = min(
                self._capacity,
                tokens + (now - last) * self._refill_per_second,
            )
            if tokens >= 1.0:
                self._buckets[ip] = (tokens - 1.0, now)
                self._maybe_prune_locked(now)
                return True, 0
            self._buckets[ip] = (tokens, now)
            retry_after = max(1, int((1.0 - tokens) / self._refill_per_second) + 1)
            self._maybe_prune_locked(now)
            return False, retry_after

    def _maybe_prune_locked(self, now: float) -> None:
        if now - self._last_prune < self._PRUNE_IDLE_SECONDS:
            return
        self._last_prune = now
        stale = [
            ip for ip, (_, last) in self._buckets.items()
            if now - last > self._PRUNE_IDLE_SECONDS
        ]
        for ip in stale:
            del self._buckets[ip]

    @property
    def bucket_count(self) -> int:
        """Number of tracked IPs. Exposed for tests and metrics."""
        with self._lock:
            return len(self._buckets)
