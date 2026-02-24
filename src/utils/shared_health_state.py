"""
MeshForge Maps - Shared Health State Reader

Read-only access to MeshForge core's shared health state database.
Enables the maps extension to display health information from the
main MeshForge process (gateway bridge status, node tracker health,
service states) without direct coupling.

MeshForge core writes to: ~/.config/meshforge/health_state.db
This module provides read-only access using SQLite WAL mode for
lock-free concurrent reads.

Modeled after meshforge core's utils/shared_health_state.py:
  - SQLite WAL mode for concurrent access
  - Read-only connection (no writes to core's DB)
  - Graceful fallback when DB doesn't exist
  - Latency percentile reads
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import get_config_dir

logger = logging.getLogger(__name__)

# Default location of MeshForge core's health state DB
DEFAULT_HEALTH_DB_PATH = get_config_dir() / "health_state.db"


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
            # Open in read-only mode using URI
            uri = f"file:{self._db_path}?mode=ro"
            conn = sqlite3.connect(
                uri, uri=True, check_same_thread=False,
            )
            conn.execute("PRAGMA busy_timeout=1000")
            with self._lock:
                self._conn = conn
                self._available = True
            logger.info("Connected to shared health DB at %s", self._db_path)
        except Exception as e:
            logger.debug("Failed to open shared health DB: %s", e)
            with self._lock:
                self._conn = None

    @property
    def available(self) -> bool:
        """Whether the shared health DB is accessible."""
        with self._lock:
            return self._available and self._conn is not None

    def get_service_states(self) -> List[Dict[str, Any]]:
        """Read all service health states from the core DB.

        Returns a list of dicts with service_name, status, last_updated, etc.
        """
        if not self._conn:
            return []

        with self._lock:
            try:
                rows = self._conn.execute(
                    """SELECT service_name, status, last_updated,
                              error_count, success_count, metadata
                       FROM service_health
                       ORDER BY service_name"""
                ).fetchall()
                return [
                    {
                        "service_name": r[0],
                        "status": r[1],
                        "last_updated": r[2],
                        "error_count": r[3],
                        "success_count": r[4],
                        "metadata": r[5],
                    }
                    for r in rows
                ]
            except sqlite3.OperationalError:
                # Table doesn't exist or schema mismatch
                return []
            except Exception as e:
                logger.debug("Service states read failed: %s", e)
                return []

    def get_node_health(self, node_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Read node health records from core.

        If node_id is provided, returns health for that node only.
        Otherwise returns all node health records.
        """
        if not self._conn:
            return []

        with self._lock:
            try:
                if node_id:
                    rows = self._conn.execute(
                        """SELECT node_id, health_score, status, last_seen,
                                  network, metadata
                           FROM node_health WHERE node_id = ?""",
                        (node_id,),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        """SELECT node_id, health_score, status, last_seen,
                                  network, metadata
                           FROM node_health ORDER BY last_seen DESC"""
                    ).fetchall()
                return [
                    {
                        "node_id": r[0],
                        "health_score": r[1],
                        "status": r[2],
                        "last_seen": r[3],
                        "network": r[4],
                        "metadata": r[5],
                    }
                    for r in rows
                ]
            except sqlite3.OperationalError:
                return []
            except Exception as e:
                logger.debug("Node health read failed: %s", e)
                return []

    def get_latency_percentiles(self) -> Dict[str, Any]:
        """Read message delivery latency percentiles from core.

        Returns dict with p50, p90, p99, and sample_count.
        """
        if not self._conn:
            return {}

        with self._lock:
            try:
                row = self._conn.execute(
                    """SELECT p50_ms, p90_ms, p99_ms, sample_count,
                              last_updated
                       FROM latency_stats ORDER BY last_updated DESC LIMIT 1"""
                ).fetchone()
                if not row:
                    return {}
                return {
                    "p50_ms": row[0],
                    "p90_ms": row[1],
                    "p99_ms": row[2],
                    "sample_count": row[3],
                    "last_updated": row[4],
                }
            except sqlite3.OperationalError:
                return {}
            except Exception as e:
                logger.debug("Latency percentiles read failed: %s", e)
                return {}

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of all shared health state data.

        Returns a dict suitable for API responses with service states,
        latency stats, and a timestamp.
        """
        return {
            "available": self.available,
            "services": self.get_service_states(),
            "latency": self.get_latency_percentiles(),
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
