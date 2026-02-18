"""Tests for performance monitoring."""

import time

import pytest

from src.utils.perf_monitor import PerfMonitor


class TestPerfMonitorRecording:
    """Tests for recording timing data."""

    @pytest.fixture
    def monitor(self):
        return PerfMonitor()

    def test_record_timing(self, monitor):
        monitor.record_timing("source_a", 100.0, node_count=5)
        stats = monitor.get_source_stats("source_a")
        assert stats is not None
        assert stats["count"] == 1
        assert stats["avg_ms"] == 100.0
        assert stats["total_nodes_collected"] == 5

    def test_record_multiple_timings(self, monitor):
        monitor.record_timing("source_a", 100.0)
        monitor.record_timing("source_a", 200.0)
        monitor.record_timing("source_a", 300.0)
        stats = monitor.get_source_stats("source_a")
        assert stats["count"] == 3
        assert stats["avg_ms"] == 200.0
        assert stats["min_ms"] == 100.0
        assert stats["max_ms"] == 300.0

    def test_record_many_timings(self, monitor):
        for i in range(10):
            monitor.record_timing("source_a", float(i))
        stats = monitor.get_source_stats("source_a")
        # Counter-based: all recordings are accumulated
        assert stats["count"] == 10

    def test_record_cycle(self, monitor):
        monitor.record_cycle(500.0, total_nodes=42)
        stats = monitor.get_stats()
        assert stats["total_collections"] == 1
        assert stats["cycle"] is not None
        assert stats["cycle"]["count"] == 1
        assert stats["cycle"]["total_nodes_collected"] == 42

    def test_unknown_source_returns_none(self, monitor):
        assert monitor.get_source_stats("nonexistent") is None

    def test_cache_hit_ratio(self, monitor):
        monitor.record_timing("source_a", 10.0, from_cache=True)
        monitor.record_timing("source_a", 100.0, from_cache=False)
        stats = monitor.get_source_stats("source_a")
        assert stats["cache_hit_ratio"] == 0.5


class TestTimingContext:
    """Tests for the timing context manager."""

    def test_time_collection(self):
        monitor = PerfMonitor()
        with monitor.time_collection("test_source") as ctx:
            time.sleep(0.01)  # ~10ms
            ctx.node_count = 7
        stats = monitor.get_source_stats("test_source")
        assert stats is not None
        assert stats["count"] == 1
        assert stats["avg_ms"] >= 5.0  # At least 5ms
        assert stats["total_nodes_collected"] == 7

    def test_time_cycle(self):
        monitor = PerfMonitor()
        with monitor.time_cycle() as ctx:
            time.sleep(0.01)
            ctx.node_count = 20
        stats = monitor.get_stats()
        assert stats["total_collections"] == 1
        assert stats["cycle"]["avg_ms"] >= 5.0
        assert stats["cycle"]["total_nodes_collected"] == 20

    def test_context_records_cache_flag(self):
        monitor = PerfMonitor()
        with monitor.time_collection("cached") as ctx:
            ctx.from_cache = True
        stats = monitor.get_source_stats("cached")
        assert stats["cache_hit_ratio"] == 1.0


class TestGetStats:
    """Tests for comprehensive stats reporting."""

    def test_stats_with_data(self):
        monitor = PerfMonitor()
        monitor.record_timing("meshtastic", 50.0, node_count=10)
        monitor.record_timing("reticulum", 30.0, node_count=5)
        monitor.record_cycle(100.0, total_nodes=15)
        stats = monitor.get_stats()
        assert stats["total_collections"] == 1
        assert "meshtastic" in stats["sources"]
        assert "reticulum" in stats["sources"]
        assert stats["cycle"] is not None
        assert stats["memory"]["tracked_sources"] == 2

    def test_collections_per_minute(self):
        monitor = PerfMonitor()
        monitor._start_time = time.time() - 60  # Started 1 min ago
        monitor.record_cycle(100.0)
        monitor.record_cycle(100.0)
        stats = monitor.get_stats()
        assert stats["collections_per_minute"] == pytest.approx(2.0, abs=0.5)

    def test_last_duration_and_timestamp(self):
        monitor = PerfMonitor()
        monitor.record_timing("s", 10.0)
        monitor.record_timing("s", 20.0)
        monitor.record_timing("s", 30.0)
        stats = monitor.get_source_stats("s")
        assert stats["last_duration_ms"] == 30.0


class TestMemoryUsage:
    """Tests for memory usage reporting."""

    def test_memory_with_data(self):
        monitor = PerfMonitor()
        monitor.record_timing("a", 10.0)
        monitor.record_timing("a", 20.0)
        monitor.record_timing("b", 30.0)
        mem = monitor.get_memory_usage()
        assert mem["tracked_sources"] == 2
