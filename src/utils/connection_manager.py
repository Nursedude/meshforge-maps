"""
MeshForge Maps - Meshtastic Connection Manager

Manages exclusive access to the meshtasticd TCP connection (localhost:4403).
Meshtasticd only supports a single TCP client at a time; if MeshForge core's
gateway is already connected, the maps collector must wait or use cache.

Architecture aligned with meshforge core's utils/connection_manager.py:
  - Thread-safe singleton lock per host:port
  - Configurable acquire timeout (default 5s)
  - Context manager for automatic release
  - Connection state tracking for diagnostics

This prevents "Connection refused" / "broken pipe" errors when both
MeshForge core and maps try to talk to meshtasticd simultaneously.
"""

import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Thread-safe singleton connection lock for meshtasticd.

    Only one component at a time should hold the connection to meshtasticd's
    TCP socket. This manager provides a cooperative lock with timeout.

    Usage:
        mgr = ConnectionManager.get_instance("localhost", 4403)
        with mgr.acquire(timeout=5.0) as acquired:
            if acquired:
                # Safe to connect to meshtasticd
                data = fetch_from_meshtasticd()
            else:
                # Another component holds the connection
                data = use_cache_fallback()
    """

    _instances: Dict[str, "ConnectionManager"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._lock = threading.Lock()
        self._holder: Optional[str] = None
        self._acquire_time: float = 0
        self._total_acquisitions: int = 0
        self._total_timeouts: int = 0
        self._total_releases: int = 0

    @classmethod
    def get_instance(cls, host: str = "localhost", port: int = 4403) -> "ConnectionManager":
        """Get or create the singleton ConnectionManager for a host:port pair."""
        key = f"{host}:{port}"
        with cls._instances_lock:
            if key not in cls._instances:
                cls._instances[key] = cls(host, port)
            return cls._instances[key]

    @classmethod
    def reset_all(cls) -> None:
        """Reset all instances (for testing)."""
        with cls._instances_lock:
            cls._instances.clear()

    def acquire(self, timeout: float = 5.0, holder: str = "") -> "_ConnectionContext":
        """Return a context manager that tries to acquire the connection lock.

        Args:
            timeout: Max seconds to wait for the lock. 0 = non-blocking try.
            holder: Optional name identifying who is acquiring (for diagnostics).

        Returns:
            A context manager. The __enter__ method returns True if the lock
            was acquired, False if it timed out.
        """
        return _ConnectionContext(self, timeout, holder)

    def _try_acquire(self, timeout: float, holder: str) -> bool:
        """Internal: attempt to acquire the lock within timeout."""
        acquired = self._lock.acquire(timeout=timeout if timeout > 0 else 0)
        if acquired:
            self._holder = holder or "unknown"
            self._acquire_time = time.time()
            self._total_acquisitions += 1
            logger.debug(
                "Connection lock acquired by '%s' for %s:%d",
                self._holder, self._host, self._port,
            )
            return True
        self._total_timeouts += 1
        logger.debug(
            "Connection lock timeout (%.1fs) for %s:%d, held by '%s'",
            timeout, self._host, self._port, self._holder or "unknown",
        )
        return False

    def _release(self) -> None:
        """Internal: release the connection lock."""
        holder = self._holder
        self._holder = None
        self._acquire_time = 0
        self._total_releases += 1
        try:
            self._lock.release()
        except RuntimeError:
            pass  # Already released
        logger.debug(
            "Connection lock released by '%s' for %s:%d",
            holder or "unknown", self._host, self._port,
        )

    @property
    def is_locked(self) -> bool:
        """Check if the connection is currently held (non-blocking)."""
        if self._lock.acquire(timeout=0):
            self._lock.release()
            return False
        return True

    @property
    def holder(self) -> Optional[str]:
        """Name of the current lock holder, or None."""
        return self._holder

    @property
    def stats(self) -> Dict[str, Any]:
        """Diagnostic stats for the connection manager."""
        held_seconds = None
        if self._acquire_time > 0:
            held_seconds = round(time.time() - self._acquire_time, 1)
        return {
            "host": self._host,
            "port": self._port,
            "is_locked": self.is_locked,
            "holder": self._holder,
            "held_seconds": held_seconds,
            "total_acquisitions": self._total_acquisitions,
            "total_timeouts": self._total_timeouts,
            "total_releases": self._total_releases,
        }


class _ConnectionContext:
    """Context manager returned by ConnectionManager.acquire()."""

    def __init__(self, manager: ConnectionManager, timeout: float, holder: str):
        self._manager = manager
        self._timeout = timeout
        self._holder = holder
        self._acquired = False

    def __enter__(self) -> bool:
        self._acquired = self._manager._try_acquire(self._timeout, self._holder)
        return self._acquired

    def __exit__(self, *exc: Any) -> None:
        if self._acquired:
            self._manager._release()
            self._acquired = False
