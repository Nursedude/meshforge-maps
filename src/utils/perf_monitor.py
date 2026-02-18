"""
MeshForge Maps - Performance Monitor

Instruments collection cycle timing and per-source latency for runtime
diagnostics. Keeps simple counters and last/average timing -- no percentile
calculations or bounded sample history.

Thread-safe: all state behind a lock.
"""

import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class PerfMonitor:
    """Tracks collection timing with simple counters.

    Usage:
        monitor = PerfMonitor()

        # Time a collection
        with monitor.time_collection("meshtastic") as ctx:
            data = collector.collect()
            ctx.node_count = len(data.get("features", []))

        # Get stats
        stats = monitor.get_stats()
    """

    def __init__(self, max_samples: int = 100):
        # max_samples accepted for backward compat but not used
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._total_collections = 0
        self._sources: Dict[str, Dict[str, Any]] = {}
        self._cycle: Optional[Dict[str, Any]] = None

    def record_timing(
        self,
        source: str,
        duration_ms: float,
        node_count: int = 0,
        from_cache: bool = False,
    ) -> None:
        """Record a timing sample for a source."""
        with self._lock:
            if source not in self._sources:
                self._sources[source] = {
                    "count": 0, "total_ms": 0.0,
                    "cache_hits": 0, "total_nodes": 0,
                    "last_ms": 0.0, "last_time": 0.0,
                    "min_ms": float("inf"), "max_ms": 0.0,
                }
            s = self._sources[source]
            s["count"] += 1
            s["total_ms"] += duration_ms
            s["last_ms"] = duration_ms
            s["last_time"] = time.time()
            s["total_nodes"] += node_count
            if from_cache:
                s["cache_hits"] += 1
            if duration_ms < s["min_ms"]:
                s["min_ms"] = duration_ms
            if duration_ms > s["max_ms"]:
                s["max_ms"] = duration_ms

    def record_cycle(self, duration_ms: float, total_nodes: int = 0) -> None:
        """Record a full collection cycle timing."""
        with self._lock:
            if self._cycle is None:
                self._cycle = {
                    "count": 0, "total_ms": 0.0,
                    "last_ms": 0.0, "total_nodes": 0,
                }
            self._cycle["count"] += 1
            self._cycle["total_ms"] += duration_ms
            self._cycle["last_ms"] = duration_ms
            self._cycle["total_nodes"] += total_nodes
            self._total_collections += 1

    def time_collection(self, source: str) -> "TimingContext":
        """Context manager for timing a collection."""
        return TimingContext(self, source)

    def time_cycle(self) -> "TimingContext":
        """Context manager for timing a full collection cycle."""
        return TimingContext(self, "_cycle", is_cycle=True)

    def get_source_stats(self, source: str) -> Optional[Dict[str, Any]]:
        """Get timing stats for a specific source."""
        with self._lock:
            s = self._sources.get(source)
            if not s:
                return None
            return self._format_source(source, s)

    def get_stats(self) -> Dict[str, Any]:
        """Get performance statistics."""
        with self._lock:
            uptime = time.time() - self._start_time

            source_stats = {
                name: self._format_source(name, s)
                for name, s in self._sources.items()
            }

            cycle_stats = None
            if self._cycle and self._cycle["count"]:
                c = self._cycle
                cycle_stats = {
                    "source": "_cycle",
                    "count": c["count"],
                    "avg_ms": round(c["total_ms"] / c["count"], 2),
                    "last_duration_ms": round(c["last_ms"], 2),
                    "total_nodes_collected": c["total_nodes"],
                }

            return {
                "uptime_seconds": int(uptime),
                "total_collections": self._total_collections,
                "collections_per_minute": round(
                    self._total_collections / max(uptime / 60, 1), 2
                ),
                "sources": source_stats,
                "cycle": cycle_stats,
                "memory": {
                    "tracked_sources": len(self._sources),
                },
            }

    @staticmethod
    def _format_source(name: str, s: Dict[str, Any]) -> Dict[str, Any]:
        count = s["count"]
        return {
            "source": name,
            "count": count,
            "avg_ms": round(s["total_ms"] / count, 2) if count else 0,
            "min_ms": round(s["min_ms"], 2) if s["min_ms"] != float("inf") else 0,
            "max_ms": round(s["max_ms"], 2),
            "last_duration_ms": round(s["last_ms"], 2),
            "last_timestamp": s["last_time"],
            "cache_hit_ratio": round(s["cache_hits"] / count, 3) if count else 0,
            "total_nodes_collected": s["total_nodes"],
        }

    def get_memory_usage(self) -> Dict[str, Any]:
        """Get current memory usage estimates."""
        with self._lock:
            return {"tracked_sources": len(self._sources)}


class TimingContext:
    """Context manager for timing operations."""

    def __init__(self, monitor: PerfMonitor, source: str, is_cycle: bool = False):
        self._monitor = monitor
        self._source = source
        self._is_cycle = is_cycle
        self._start: float = 0
        self.node_count: int = 0
        self.from_cache: bool = False

    def __enter__(self) -> "TimingContext":
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        elapsed_ms = (time.monotonic() - self._start) * 1000
        if self._is_cycle:
            self._monitor.record_cycle(elapsed_ms, self.node_count)
        else:
            self._monitor.record_timing(
                self._source, elapsed_ms, self.node_count, self.from_cache
            )
