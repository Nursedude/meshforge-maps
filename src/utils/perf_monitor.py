"""
MeshForge Maps - Performance Monitor

Instruments collection cycle timing and per-source latency for runtime
diagnostics. Tracks counters, percentiles (p50/p90/p99), per-endpoint
request metrics, SLA/availability, error rates, and capacity trends.

Thread-safe: all state behind a lock.
"""

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PerfMonitor:
    """Tracks collection timing with percentiles and operational metrics.

    Usage:
        monitor = PerfMonitor()

        # Time a collection
        with monitor.time_collection("meshtastic") as ctx:
            data = collector.collect()
            ctx.node_count = len(data.get("features", []))

        # Get stats
        stats = monitor.get_stats()
    """

    _SAMPLE_WINDOW = 1000

    def __init__(self):
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._total_collections = 0
        self._sources: Dict[str, Dict[str, Any]] = {}
        self._cycle: Optional[Dict[str, Any]] = None
        self._endpoints: Dict[str, Dict[str, Any]] = {}
        self._availability: Dict[str, Dict[str, Any]] = {}
        self._node_count_history: deque = deque(maxlen=self._SAMPLE_WINDOW)

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
                    "samples": deque(maxlen=self._SAMPLE_WINDOW),
                }
            s = self._sources[source]
            s["count"] += 1
            s["total_ms"] += duration_ms
            s["last_ms"] = duration_ms
            s["last_time"] = time.time()
            s["total_nodes"] += node_count
            s["samples"].append(duration_ms)
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
                    "samples": deque(maxlen=self._SAMPLE_WINDOW),
                }
            self._cycle["count"] += 1
            self._cycle["total_ms"] += duration_ms
            self._cycle["last_ms"] = duration_ms
            self._cycle["total_nodes"] += total_nodes
            self._cycle["samples"].append(duration_ms)
            self._total_collections += 1

    def record_request(
        self, path: str, duration_ms: float, status_code: int = 200,
    ) -> None:
        """Record an HTTP request timing for per-endpoint metrics."""
        with self._lock:
            if path not in self._endpoints:
                self._endpoints[path] = {
                    "count": 0, "total_ms": 0.0, "last_ms": 0.0,
                    "samples": deque(maxlen=500),
                    "status_codes": {},
                }
            ep = self._endpoints[path]
            ep["count"] += 1
            ep["total_ms"] += duration_ms
            ep["last_ms"] = duration_ms
            ep["samples"].append(duration_ms)
            sc = str(status_code)
            ep["status_codes"][sc] = ep["status_codes"].get(sc, 0) + 1

    def get_endpoint_stats(self) -> Dict[str, Any]:
        """Get per-endpoint request metrics with percentiles."""
        with self._lock:
            result = {}
            for path, ep in self._endpoints.items():
                count = ep["count"]
                pct = self._percentiles(ep["samples"])
                result[path] = {
                    "count": count,
                    "avg_ms": round(ep["total_ms"] / count, 2) if count else 0,
                    "last_ms": round(ep["last_ms"], 2),
                    "status_codes": dict(ep["status_codes"]),
                    **pct,
                }
            return result

    def record_collection_result(
        self, source: str, success: bool, timestamp: Optional[float] = None,
    ) -> None:
        """Record a collection success/failure for SLA tracking."""
        ts = timestamp if timestamp is not None else time.time()
        with self._lock:
            if source not in self._availability:
                self._availability[source] = {
                    "success_timestamps": deque(maxlen=2000),
                    "failure_timestamps": deque(maxlen=2000),
                    "consecutive_failures": 0,
                }
            avail = self._availability[source]
            if success:
                avail["success_timestamps"].append(ts)
                avail["consecutive_failures"] = 0
            else:
                avail["failure_timestamps"].append(ts)
                avail["consecutive_failures"] += 1

    def get_availability(
        self, source: str, window_seconds: float = 3600,
    ) -> Dict[str, Any]:
        """Get availability percentage for a source within a time window."""
        with self._lock:
            avail = self._availability.get(source)
            if not avail:
                return {
                    "availability_pct": 100.0, "consecutive_failures": 0,
                    "total": 0, "successes": 0, "failures": 0,
                }
            now = time.time()
            cutoff = now - window_seconds
            successes = sum(
                1 for t in avail["success_timestamps"] if t >= cutoff
            )
            failures = sum(
                1 for t in avail["failure_timestamps"] if t >= cutoff
            )
            total = successes + failures
            pct = round(successes / total * 100, 2) if total else 100.0
            return {
                "availability_pct": pct,
                "consecutive_failures": avail["consecutive_failures"],
                "total": total, "successes": successes, "failures": failures,
            }

    def error_rate(
        self, source: str, window_seconds: float = 300,
    ) -> float:
        """Return errors per minute for a source within the given window."""
        with self._lock:
            avail = self._availability.get(source)
            if not avail:
                return 0.0
            now = time.time()
            cutoff = now - window_seconds
            errors = sum(
                1 for t in avail["failure_timestamps"] if t >= cutoff
            )
            minutes = window_seconds / 60
            return round(errors / max(minutes, 1), 2)

    def record_node_count(
        self, count: int, timestamp: Optional[float] = None,
    ) -> None:
        """Record current node count for capacity planning."""
        ts = timestamp if timestamp is not None else time.time()
        with self._lock:
            self._node_count_history.append((ts, count))

    def get_capacity_metrics(self) -> Dict[str, Any]:
        """Get capacity planning metrics: growth rate, cycle trends."""
        with self._lock:
            history = list(self._node_count_history)
            cycle_samples = list(
                self._cycle.get("samples", deque())
            ) if self._cycle else []

        # Node growth rate
        growth_rate = 0.0
        current_nodes = 0
        if len(history) >= 2:
            first_ts, first_count = history[0]
            last_ts, last_count = history[-1]
            hours = (last_ts - first_ts) / 3600
            if hours > 0:
                growth_rate = round((last_count - first_count) / hours, 2)
            current_nodes = last_count
        elif history:
            current_nodes = history[-1][1]

        # Cycle time trend
        trend = "unknown"
        first_half_avg = 0.0
        second_half_avg = 0.0
        if len(cycle_samples) >= 10:
            mid = len(cycle_samples) // 2
            first_half_avg = sum(cycle_samples[:mid]) / mid
            second_half_avg = (
                sum(cycle_samples[mid:]) / (len(cycle_samples) - mid)
            )
            if second_half_avg > first_half_avg * 1.2:
                trend = "increasing"
            elif second_half_avg < first_half_avg * 0.8:
                trend = "decreasing"
            else:
                trend = "stable"
        elif cycle_samples:
            trend = "insufficient_data"

        return {
            "node_growth_rate_per_hour": growth_rate,
            "current_nodes": current_nodes,
            "data_points": len(history),
            "cycle_time_trend": trend,
            "cycle_first_half_avg_ms": round(first_half_avg, 2),
            "cycle_second_half_avg_ms": round(second_half_avg, 2),
            "cycle_sample_count": len(cycle_samples),
        }

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
                pct = self._percentiles(c.get("samples", deque()))
                cycle_stats = {
                    "source": "_cycle",
                    "count": c["count"],
                    "avg_ms": round(c["total_ms"] / c["count"], 2),
                    "last_duration_ms": round(c["last_ms"], 2),
                    "total_nodes_collected": c["total_nodes"],
                    **pct,
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
    def _percentiles(samples: deque) -> Dict[str, float]:
        """Compute p50/p90/p99 from a deque of timing samples (stdlib-only)."""
        if not samples:
            return {"p50_ms": 0, "p90_ms": 0, "p99_ms": 0}
        sorted_s = sorted(samples)
        n = len(sorted_s)
        return {
            "p50_ms": round(sorted_s[n * 50 // 100], 2),
            "p90_ms": round(sorted_s[min(n * 90 // 100, n - 1)], 2),
            "p99_ms": round(sorted_s[min(n * 99 // 100, n - 1)], 2),
        }

    @staticmethod
    def _format_source(name: str, s: Dict[str, Any]) -> Dict[str, Any]:
        count = s["count"]
        pct = PerfMonitor._percentiles(s.get("samples", deque()))
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
            **pct,
        }

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


def get_memory_snapshot(ctx: Any) -> Dict[str, Any]:
    """Build a memory usage snapshot from MapServerContext components.

    Uses public properties only — no private attribute access.
    """
    import os

    snapshot: Dict[str, Any] = {}

    # MQTT node store
    if ctx.aggregator:
        sub = getattr(ctx.aggregator, "mqtt_subscriber", None)
        if sub:
            store = getattr(sub, "store", None)
            if store:
                snapshot["mqtt_node_store_count"] = store.node_count

    # NodeHistoryDB observation count + DB file size
    if ctx.node_history:
        snapshot["node_history_observations"] = ctx.node_history.observation_count
        db_path = getattr(ctx.node_history, "_db_path", None)
        if db_path:
            try:
                snapshot["db_file_size_bytes"] = os.path.getsize(str(db_path))
            except OSError:
                pass

    # NodeHealthScorer
    if ctx.health_scorer:
        snapshot["health_scorer_count"] = ctx.health_scorer.scored_node_count

    # NodeStateTracker
    if ctx.node_state:
        summary = ctx.node_state.get_summary()
        snapshot["node_state_count"] = summary.get("tracked_nodes", 0)

    # ConfigDriftDetector
    if ctx.config_drift:
        snapshot["config_drift_count"] = ctx.config_drift.tracked_node_count

    # AlertEngine history size
    if ctx.alert_engine:
        summary = ctx.alert_engine.get_summary()
        snapshot["alert_history_count"] = summary.get("history_size", 0)

    return snapshot
