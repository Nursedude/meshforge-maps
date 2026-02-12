"""
MeshForge Maps - Performance Monitor

Instruments collection cycle timing, per-source latency, and memory usage
for runtime diagnostics. All metrics are kept in-memory with bounded history.

Thread-safe: all state behind a lock.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Maximum timing samples to retain per source
MAX_SAMPLES = 100


class TimingSample:
    """A single timing measurement."""

    __slots__ = ("source", "duration_ms", "node_count", "from_cache", "timestamp")

    def __init__(
        self,
        source: str,
        duration_ms: float,
        node_count: int = 0,
        from_cache: bool = False,
        timestamp: Optional[float] = None,
    ):
        self.source = source
        self.duration_ms = duration_ms
        self.node_count = node_count
        self.from_cache = from_cache
        self.timestamp = timestamp or time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "duration_ms": round(self.duration_ms, 2),
            "node_count": self.node_count,
            "from_cache": self.from_cache,
            "timestamp": self.timestamp,
        }


class PerfMonitor:
    """Tracks collection timing and memory usage.

    Usage:
        monitor = PerfMonitor()

        # Time a collection
        with monitor.time_collection("meshtastic") as ctx:
            data = collector.collect()
            ctx.node_count = len(data.get("features", []))

        # Get stats
        stats = monitor.get_stats()
    """

    def __init__(self, max_samples: int = MAX_SAMPLES):
        self._max_samples = max_samples
        self._samples: Dict[str, List[TimingSample]] = {}
        self._cycle_samples: List[TimingSample] = []  # Full cycle timings
        self._lock = threading.Lock()
        self._total_collections = 0
        self._start_time = time.time()

    def record_timing(
        self,
        source: str,
        duration_ms: float,
        node_count: int = 0,
        from_cache: bool = False,
    ) -> None:
        """Record a timing sample for a source."""
        sample = TimingSample(source, duration_ms, node_count, from_cache)
        with self._lock:
            if source not in self._samples:
                self._samples[source] = []
            samples = self._samples[source]
            samples.append(sample)
            if len(samples) > self._max_samples:
                self._samples[source] = samples[-self._max_samples:]

    def record_cycle(self, duration_ms: float, total_nodes: int = 0) -> None:
        """Record a full collection cycle timing."""
        sample = TimingSample("_cycle", duration_ms, total_nodes)
        with self._lock:
            self._cycle_samples.append(sample)
            if len(self._cycle_samples) > self._max_samples:
                self._cycle_samples = self._cycle_samples[-self._max_samples:]
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
            samples = self._samples.get(source)
            if not samples:
                return None
            return self._compute_stats(samples, source)

    def get_memory_usage(self) -> Dict[str, Any]:
        """Get current memory usage estimates.

        Uses sys.getsizeof for shallow size estimates of key data structures.
        Not a deep profiler — gives approximate memory footprint.
        """
        return {
            "timing_samples": sum(
                len(s) for s in self._samples.values()
            ),
            "cycle_samples": len(self._cycle_samples),
            "tracked_sources": len(self._samples),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive performance statistics."""
        with self._lock:
            uptime = time.time() - self._start_time

            # Per-source stats
            source_stats = {}
            for source, samples in self._samples.items():
                source_stats[source] = self._compute_stats(samples, source)

            # Cycle stats
            cycle_stats = None
            if self._cycle_samples:
                cycle_stats = self._compute_stats(self._cycle_samples, "_cycle")

            return {
                "uptime_seconds": int(uptime),
                "total_collections": self._total_collections,
                "collections_per_minute": round(
                    self._total_collections / max(uptime / 60, 1), 2
                ),
                "sources": source_stats,
                "cycle": cycle_stats,
                "memory": self.get_memory_usage(),
            }

    def _compute_stats(
        self, samples: List[TimingSample], source: str
    ) -> Dict[str, Any]:
        """Compute aggregate stats from a list of timing samples."""
        if not samples:
            return {"source": source, "count": 0}

        durations = [s.duration_ms for s in samples]
        cache_count = sum(1 for s in samples if s.from_cache)
        total_nodes = sum(s.node_count for s in samples)
        sorted_d = sorted(durations)
        count = len(sorted_d)

        p50_idx = int(count * 0.5)
        p90_idx = min(int(count * 0.9), count - 1)
        p99_idx = min(int(count * 0.99), count - 1)

        return {
            "source": source,
            "count": count,
            "avg_ms": round(sum(durations) / count, 2),
            "min_ms": round(sorted_d[0], 2),
            "max_ms": round(sorted_d[-1], 2),
            "p50_ms": round(sorted_d[p50_idx], 2),
            "p90_ms": round(sorted_d[p90_idx], 2),
            "p99_ms": round(sorted_d[p99_idx], 2),
            "cache_hit_ratio": round(cache_count / count, 3) if count else 0,
            "total_nodes_collected": total_nodes,
            "last_duration_ms": round(samples[-1].duration_ms, 2),
            "last_timestamp": samples[-1].timestamp,
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
