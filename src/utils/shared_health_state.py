"""
MeshForge Maps - Shared Health State Reader

Read-only access to MeshForge core's shared health state database.
Enables the maps extension to display health information from the
main MeshForge process (gateway bridge status, node tracker health,
service states) without direct coupling.

MeshForge core writes to: ~/.config/meshforge/health_state.db
This module provides read-only access using SQLite WAL mode for
lock-free concurrent reads.

Reads MeshForge core's REAL schema (utils/shared_health_state.py there):
service_health + latency_samples. tests/test_core_health_contract.py builds
the DB with core's own SharedHealthState — never a test-invented schema
(domain audit 2026-10-18: the old reader queried tables core never creates,
swallowed the error and served an empty, healthy-looking view).
  - Read-only connection (no writes to core's DB)
  - Tri-state status: no_db / unreadable / empty / ok — never a bare []
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .db_helpers import connect_tuned
from .paths import get_config_dir

logger = logging.getLogger(__name__)

# Default location of MeshForge core's health state DB
DEFAULT_HEALTH_DB_PATH = get_config_dir() / "health_state.db"

# MeshForge core's own staleness rule (its SharedHealthState
# DEFAULT_STALE_THRESHOLD): a row not updated this long is "can't tell".
STALE_AFTER_S = 120
# Latency percentiles look back this far (core's get_latency_percentiles: 1 h).
LATENCY_WINDOW_S = 3600
# A row stamped more than this far in the FUTURE is clock skew, not fresh.
CLOCK_SKEW_TOLERANCE_S = 5


class SharedHealthStateReader:
    """Read-only access to MeshForge core's shared health state.

    Opens the database in read-only mode with WAL journal for
    non-blocking concurrent reads. All operations are thread-safe.

    If the database doesn't exist (MeshForge core not installed or
    not yet run), all methods return empty/default values.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DEFAULT_HEALTH_DB_PATH
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._available = False
        self._last_error: Optional[str] = None
        self._closed = False
        self._connect()

    def _connect(self) -> None:
        """Open a read-only connection to the health state DB.

        Thread-safe: acquires self._lock around state mutations.
        """
        if not self._db_path.exists():
            logger.debug(
                "Shared health DB not found at %s (core not running?)",
                self._db_path,
            )
            return

        try:
            # Read-only URI mode via the shared helper — keeps every new
            # SQLite consumer on a single connection pattern (per CLAUDE.md
            # "anything new starts with connect_tuned"). 1 s busy timeout
            # is the historical knob for this DB; tighter than the 30 s
            # default so a stalled writer in core doesn't lock the request
            # path here.
            conn = connect_tuned(
                self._db_path,
                check_same_thread=False,
                read_only=True,
                busy_timeout_seconds=1.0,
            )
            with self._lock:
                self._conn = conn
                self._available = True
                self._last_error = None
            logger.info("Connected to shared health DB at %s", self._db_path)
        except sqlite3.Error as e:
            # The file EXISTS: this is "can't read", never "absent".
            if str(e) != self._last_error:
                logger.warning("Core health DB %s exists but cannot be "
                               "opened: %s", self._db_path, e)
            with self._lock:
                self._conn = None
                self._last_error = str(e)

    @property
    def available(self) -> bool:
        """Whether a connection to the DB file is OPEN. Not the same as the
        summary's ``available`` (= its contents read as core's schema): a
        garbage file is open-able but ``unreadable``."""
        with self._lock:
            return self._available and self._conn is not None

    def _read(self):
        """``(status, error, services, latency)`` — tri-state, never a bare
        ``[]`` (domain audit 2026-10-18: a read error used to come back as an
        empty list, which reads as "no problems").

        status: ``closed`` (``close()`` was called) · ``no_db`` (core's DB
        absent) · ``unreadable`` (it exists but
        cannot be read as MeshForge's schema — ``error`` says why) ·
        ``empty`` (core is there and has recorded nothing) · ``ok``.
        """
        if self._conn is None and not self._closed and self._db_path.exists():
            # The DB appeared after this reader was made: TRY the open, so
            # "unreadable" only ever follows a real failed open.
            self._connect()
        with self._lock:
            conn = self._conn
            if conn is None:
                if self._closed:
                    return "closed", None, [], {}
                if self._db_path.exists():
                    return ("unreadable", self._last_error or "cannot open",
                            [], {})
                return "no_db", None, [], {}
            try:
                rows = conn.execute(
                    """SELECT service, state, reason, latency_ms, updated_at,
                              consecutive_fails, uptime_pct
                       FROM service_health ORDER BY service"""
                ).fetchall()
                samples = conn.execute(
                    """SELECT service, latency_ms FROM latency_samples
                       WHERE timestamp > ? ORDER BY service, latency_ms""",
                    (time.time() - LATENCY_WINDOW_S,),
                ).fetchall()
            except sqlite3.Error as e:
                if str(e) != self._last_error:
                    self._last_error = str(e)
                    logger.warning(
                        "Core health DB %s unreadable as MeshForge's schema: "
                        "%s — reporting can't-tell", self._db_path, e)
                return "unreadable", str(e), [], {}
        self._last_error = None
        now = time.time()
        services = []
        for svc, state, reason, latency_ms, updated_at, fails, uptime in rows:
            age = now - (updated_at or 0)
            if age < -CLOCK_SKEW_TOLERANCE_S:
                # Stamped ahead of now: a wall-clock step (RTC-less Pi,
                # fake-hwclock, NTP) — no evidence of freshness.
                stale_reason = f"clock skew: row is {-age:.0f}s in the future"
            elif age > STALE_AFTER_S:
                stale_reason = f"not updated for {age:.0f}s"
            else:
                stale_reason = ""
            stale = bool(stale_reason)
            services.append({
                "service": svc,
                # A stale row is "can't tell", never its last answer.
                "state": "stale" if stale else state,
                "last_state": state,
                "stale": stale,
                "stale_reason": stale_reason,
                "age_s": round(age, 1),
                "reason": reason or "",
                "latency_ms": latency_ms,
                "consecutive_fails": fails,
                "uptime_pct": uptime,
            })
        by_service: Dict[str, List[float]] = {}
        for svc, ms in samples:
            by_service.setdefault(svc, []).append(ms)
        latency = {}
        for svc, vals in by_service.items():
            n = len(vals)  # same index rule as core's get_latency_percentiles
            latency[svc] = {"p50": vals[int(n * 0.50)], "p90": vals[int(n * 0.90)],
                            "p99": vals[int(n * 0.99)], "max": vals[-1],
                            "count": n}
        if not services:
            status = "empty"
        elif all(svc["stale"] for svc in services):
            status = "stale"  # a producer that ran once and stopped
        else:
            status = "ok"
        return status, None, services, latency

    def get_summary(self) -> Dict[str, Any]:
        """API shape for ``/api/core-health``: service states, per-service
        latency percentiles, and a ``status`` that never lets "can't read"
        pass for "nothing wrong"."""
        status, error, services, latency = self._read()
        return {
            "available": status in ("ok", "empty"),
            "status": status,
            "error": error,
            "services": services,
            "stale_count": sum(1 for svc in services if svc["stale"]),
            "latency": latency,
            "stale_after_s": STALE_AFTER_S,
            "checked_at": int(time.time()),
            "db_path": str(self._db_path),
        }

    def refresh(self) -> bool:
        """Re-check DB availability (e.g., if core started after maps).

        Returns True if the DB is now available.
        """
        with self._lock:
            if self._conn:
                return True
            if self._closed:
                return False  # close means close — never a silent reopen
        self._connect()
        with self._lock:
            return self._available

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception as e:
                    logger.debug("Error closing shared health DB: %s", e)
                self._conn = None
                self._available = False
            self._closed = True
