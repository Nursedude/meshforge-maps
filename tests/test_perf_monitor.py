"""Tests for performance monitoring."""

import time
from collections import deque
from unittest.mock import MagicMock

import pytest

from src.utils.perf_monitor import PerfMonitor, get_memory_snapshot


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


class TestPercentiles:
    """Tests for percentile computation."""

    def test_percentiles_known_set(self):
        monitor = PerfMonitor()
        for i in range(1, 101):
            monitor.record_timing("src", float(i))
        stats = monitor.get_source_stats("src")
        # [1..100] sorted, n=100: p50 = index 50 = 51, p90 = index 90 = 91
        assert stats["p50_ms"] == 51.0
        assert stats["p90_ms"] == 91.0
        assert stats["p99_ms"] == 100.0

    def test_percentiles_single_sample(self):
        monitor = PerfMonitor()
        monitor.record_timing("src", 42.0)
        stats = monitor.get_source_stats("src")
        assert stats["p50_ms"] == 42.0
        assert stats["p90_ms"] == 42.0
        assert stats["p99_ms"] == 42.0

    def test_percentiles_empty_deque(self):
        pct = PerfMonitor._percentiles(deque())
        assert pct["p50_ms"] == 0
        assert pct["p90_ms"] == 0
        assert pct["p99_ms"] == 0

    def test_percentiles_two_samples(self):
        monitor = PerfMonitor()
        monitor.record_timing("src", 10.0)
        monitor.record_timing("src", 20.0)
        stats = monitor.get_source_stats("src")
        # n=2: p50 = index 1 = 20, p99 = index 1 = 20
        assert stats["p50_ms"] == 20.0
        assert stats["p99_ms"] == 20.0

    def test_cycle_percentiles(self):
        monitor = PerfMonitor()
        for i in range(1, 51):
            monitor.record_cycle(float(i), total_nodes=1)
        stats = monitor.get_stats()
        # n=50: p50 = index 25 = 26, p90 = index 45 = 46
        assert stats["cycle"]["p50_ms"] == 26.0
        assert stats["cycle"]["p90_ms"] == 46.0

    def test_percentiles_in_source_stats(self):
        monitor = PerfMonitor()
        monitor.record_timing("test", 100.0)
        stats = monitor.get_source_stats("test")
        assert "p50_ms" in stats
        assert "p90_ms" in stats
        assert "p99_ms" in stats


class TestEndpointMetrics:
    """Tests for per-endpoint request metrics."""

    @pytest.fixture
    def monitor(self):
        return PerfMonitor()

    def test_record_request(self, monitor):
        monitor.record_request("/api/nodes/geojson", 15.5, 200)
        stats = monitor.get_endpoint_stats()
        assert "/api/nodes/geojson" in stats
        ep = stats["/api/nodes/geojson"]
        assert ep["count"] == 1
        assert ep["avg_ms"] == 15.5
        assert ep["status_codes"] == {"200": 1}

    def test_multiple_requests(self, monitor):
        monitor.record_request("/api/status", 10.0, 200)
        monitor.record_request("/api/status", 20.0, 200)
        monitor.record_request("/api/status", 30.0, 500)
        stats = monitor.get_endpoint_stats()
        ep = stats["/api/status"]
        assert ep["count"] == 3
        assert ep["avg_ms"] == 20.0
        assert ep["status_codes"] == {"200": 2, "500": 1}

    def test_endpoint_percentiles(self, monitor):
        for i in range(1, 101):
            monitor.record_request("/api/test", float(i), 200)
        stats = monitor.get_endpoint_stats()
        ep = stats["/api/test"]
        assert ep["p50_ms"] == 51.0
        assert ep["p90_ms"] == 91.0

    def test_empty_endpoint_stats(self, monitor):
        stats = monitor.get_endpoint_stats()
        assert stats == {}


class TestAvailability:
    """Tests for SLA/availability tracking."""

    @pytest.fixture
    def monitor(self):
        return PerfMonitor()

    def test_full_availability(self, monitor):
        now = time.time()
        for i in range(10):
            monitor.record_collection_result("src", True, now - i * 60)
        avail = monitor.get_availability("src", 3600)
        assert avail["availability_pct"] == 100.0
        assert avail["consecutive_failures"] == 0
        assert avail["successes"] == 10

    def test_half_availability(self, monitor):
        now = time.time()
        for i in range(10):
            success = i % 2 == 0
            monitor.record_collection_result("src", success, now - i * 60)
        avail = monitor.get_availability("src", 3600)
        assert avail["availability_pct"] == 50.0

    def test_consecutive_failures(self, monitor):
        monitor.record_collection_result("src", True)
        monitor.record_collection_result("src", False)
        monitor.record_collection_result("src", False)
        monitor.record_collection_result("src", False)
        avail = monitor.get_availability("src", 3600)
        assert avail["consecutive_failures"] == 3

    def test_consecutive_failures_reset(self, monitor):
        monitor.record_collection_result("src", False)
        monitor.record_collection_result("src", False)
        monitor.record_collection_result("src", True)
        avail = monitor.get_availability("src", 3600)
        assert avail["consecutive_failures"] == 0

    def test_unknown_source_availability(self, monitor):
        avail = monitor.get_availability("unknown", 3600)
        assert avail["availability_pct"] == 100.0
        assert avail["total"] == 0

    def test_windowed_availability(self, monitor):
        now = time.time()
        # Old failure (outside window)
        monitor.record_collection_result("src", False, now - 7200)
        # Recent successes (inside 1h window)
        for i in range(5):
            monitor.record_collection_result("src", True, now - i * 60)
        avail = monitor.get_availability("src", 3600)
        assert avail["availability_pct"] == 100.0
        assert avail["successes"] == 5


class TestErrorRate:
    """Tests for network-wide error rate."""

    @pytest.fixture
    def monitor(self):
        return PerfMonitor()

    def test_error_rate_no_errors(self, monitor):
        rate = monitor.error_rate("src", 300)
        assert rate == 0.0

    def test_error_rate_calculation(self, monitor):
        now = time.time()
        # 5 errors in the last 5 minutes
        for i in range(5):
            monitor.record_collection_result("src", False, now - i * 30)
        rate = monitor.error_rate("src", 300)
        assert rate == 1.0  # 5 errors / 5 minutes = 1/min

    def test_error_rate_windowed(self, monitor):
        now = time.time()
        # Error outside window
        monitor.record_collection_result("src", False, now - 600)
        # Error inside window
        monitor.record_collection_result("src", False, now - 60)
        rate = monitor.error_rate("src", 300)
        assert rate == pytest.approx(0.2, abs=0.01)  # 1 error / 5 min


class TestCapacityMetrics:
    """Tests for capacity planning metrics."""

    @pytest.fixture
    def monitor(self):
        return PerfMonitor()

    def test_empty_capacity(self, monitor):
        metrics = monitor.get_capacity_metrics()
        assert metrics["node_growth_rate_per_hour"] == 0.0
        assert metrics["current_nodes"] == 0
        assert metrics["cycle_time_trend"] == "unknown"

    def test_growth_rate(self, monitor):
        now = time.time()
        monitor.record_node_count(100, now - 3600)  # 1 hour ago
        monitor.record_node_count(200, now)  # now
        metrics = monitor.get_capacity_metrics()
        assert metrics["node_growth_rate_per_hour"] == pytest.approx(
            100.0, abs=1.0
        )
        assert metrics["current_nodes"] == 200

    def test_cycle_trend_stable(self, monitor):
        for i in range(20):
            monitor.record_cycle(100.0)
        metrics = monitor.get_capacity_metrics()
        assert metrics["cycle_time_trend"] == "stable"

    def test_cycle_trend_increasing(self, monitor):
        for i in range(20):
            monitor.record_cycle(float(50 + i * 10))
        metrics = monitor.get_capacity_metrics()
        assert metrics["cycle_time_trend"] == "increasing"

    def test_cycle_trend_insufficient(self, monitor):
        for i in range(3):
            monitor.record_cycle(100.0)
        metrics = monitor.get_capacity_metrics()
        assert metrics["cycle_time_trend"] == "insufficient_data"

    def test_single_node_count(self, monitor):
        monitor.record_node_count(50)
        metrics = monitor.get_capacity_metrics()
        assert metrics["current_nodes"] == 50
        assert metrics["node_growth_rate_per_hour"] == 0.0


class TestMemorySnapshot:
    """Tests for get_memory_snapshot."""

    def test_empty_context(self):
        ctx = MagicMock()
        ctx.aggregator = None
        ctx.node_history = None
        ctx.health_scorer = None
        ctx.node_state = None
        ctx.config_drift = None
        ctx.alert_engine = None
        snapshot = get_memory_snapshot(ctx)
        assert snapshot == {}

    def test_full_context(self, tmp_path):
        ctx = MagicMock()

        # Mock aggregator with mqtt_subscriber
        store = MagicMock()
        store.node_count = 150
        sub = MagicMock()
        sub.store = store
        ctx.aggregator.mqtt_subscriber = sub

        # Mock node_history
        ctx.node_history.observation_count = 5000
        ctx.node_history._db_path = tmp_path / "test.db"
        (tmp_path / "test.db").write_bytes(b"x" * 1024)

        # Mock health scorer
        ctx.health_scorer.scored_node_count = 80

        # Mock node state
        ctx.node_state.get_summary.return_value = {"tracked_nodes": 120}

        # Mock config drift
        ctx.config_drift.tracked_node_count = 90

        # Mock alert engine
        ctx.alert_engine.get_summary.return_value = {"history_size": 25}

        snapshot = get_memory_snapshot(ctx)
        assert snapshot["mqtt_node_store_count"] == 150
        assert snapshot["node_history_observations"] == 5000
        assert snapshot["db_file_size_bytes"] == 1024
        assert snapshot["health_scorer_count"] == 80
        assert snapshot["node_state_count"] == 120
        assert snapshot["config_drift_count"] == 90
        assert snapshot["alert_history_count"] == 25
