"""Tests for Phase 1 reliability integration: circuit breaker + retry in collectors."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.base import BaseCollector, make_feature, make_feature_collection
from src.collectors.aggregator import DataAggregator
from src.utils.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, CircuitState


# ==========================================================================
# Concrete test collector (reusable)
# ==========================================================================

class _TestCollector(BaseCollector):
    """Concrete BaseCollector for testing reliability features."""

    source_name = "test_reliability"

    def __init__(self, fetch_func=None, **kwargs):
        super().__init__(**kwargs)
        self._fetch_func = fetch_func or (
            lambda: make_feature_collection([], "test_reliability")
        )

    def _fetch(self):
        return self._fetch_func()


# ==========================================================================
# BaseCollector + CircuitBreaker Integration
# ==========================================================================

class TestBaseCollectorCircuitBreaker:
    """Tests for BaseCollector with circuit breaker integration."""

    def test_collect_records_success_on_circuit_breaker(self):
        cb = CircuitBreaker("test")
        c = _TestCollector(circuit_breaker=cb)
        c.collect()
        stats = cb.get_stats()
        assert stats["total_successes"] == 1
        assert stats["total_failures"] == 0

    def test_collect_records_failure_on_circuit_breaker(self):
        cb = CircuitBreaker("test", failure_threshold=5)

        def failing_fetch():
            raise ConnectionError("down")

        c = _TestCollector(fetch_func=failing_fetch, circuit_breaker=cb)
        c.collect()  # Should fail and record failure
        stats = cb.get_stats()
        assert stats["total_failures"] == 1

    def test_open_circuit_returns_stale_cache(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        call_count = [0]

        def fetch():
            call_count[0] += 1
            return make_feature_collection(
                [make_feature("n1", 1.0, 2.0, "test")], "test"
            )

        c = _TestCollector(fetch_func=fetch, circuit_breaker=cb, cache_ttl_seconds=0)
        # First collect succeeds, populates cache
        result = c.collect()
        assert result["properties"]["node_count"] == 1
        assert call_count[0] == 1

        # Trip the circuit breaker
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Force cache expiry
        c._cache_time = 0

        # Collect should return stale cache without calling _fetch
        result = c.collect()
        assert result["properties"]["node_count"] == 1
        assert call_count[0] == 1  # _fetch not called again

    def test_open_circuit_returns_empty_without_cache(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()  # Trip circuit

        c = _TestCollector(circuit_breaker=cb)
        c._cache = None  # Ensure no cache
        result = c.collect()
        assert result["features"] == []

    def test_half_open_allows_one_request(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
        call_count = [0]

        def fetch():
            call_count[0] += 1
            return make_feature_collection(
                [make_feature("n1", 1.0, 2.0, "test")], "test"
            )

        c = _TestCollector(
            fetch_func=fetch, circuit_breaker=cb, cache_ttl_seconds=0
        )

        # Trip circuit
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        time.sleep(0.15)

        # Should transition to HALF_OPEN and allow request
        result = c.collect()
        assert result["properties"]["node_count"] == 1
        assert call_count[0] == 1
        assert cb.state == CircuitState.CLOSED  # Success closes circuit


# ==========================================================================
# BaseCollector + Retry Integration
# ==========================================================================

class TestBaseCollectorRetry:
    """Tests for BaseCollector retry with backoff."""

    @patch("src.collectors.base.time.sleep")
    def test_retries_on_failure(self, mock_sleep):
        calls = [0]

        def flaky_fetch():
            calls[0] += 1
            if calls[0] < 3:
                raise ConnectionError("flaky")
            return make_feature_collection(
                [make_feature("n1", 1.0, 2.0, "test")], "test"
            )

        c = _TestCollector(fetch_func=flaky_fetch, max_retries=2)
        result = c.collect()
        # Should succeed on 3rd attempt (1 initial + 2 retries)
        assert result["properties"]["node_count"] == 1
        assert calls[0] == 3
        assert mock_sleep.call_count == 2  # 2 backoff waits

    @patch("src.collectors.base.time.sleep")
    def test_falls_back_to_cache_after_max_retries(self, mock_sleep):
        calls = [0]

        def always_fail():
            calls[0] += 1
            raise ConnectionError("permanently down")

        c = _TestCollector(fetch_func=always_fail, max_retries=2)
        # Seed cache first
        c._cache = make_feature_collection(
            [make_feature("cached", 1.0, 2.0, "test")], "test"
        )
        c._cache_time = 0  # Force expiry

        result = c.collect()
        assert result["properties"]["node_count"] == 1  # Stale cache
        assert calls[0] == 3  # 1 initial + 2 retries

    @patch("src.collectors.base.time.sleep")
    def test_no_retries_when_max_retries_zero(self, mock_sleep):
        calls = [0]

        def always_fail():
            calls[0] += 1
            raise ConnectionError("down")

        c = _TestCollector(fetch_func=always_fail, max_retries=0)
        c.collect()
        assert calls[0] == 1
        assert mock_sleep.call_count == 0

    @patch("src.collectors.base.time.sleep")
    def test_circuit_breaker_records_failure_after_all_retries(self, mock_sleep):
        cb = CircuitBreaker("test", failure_threshold=5)

        def always_fail():
            raise ConnectionError("down")

        c = _TestCollector(
            fetch_func=always_fail, circuit_breaker=cb, max_retries=2
        )
        c.collect()
        stats = cb.get_stats()
        # Only one failure recorded (after all retries exhausted)
        assert stats["total_failures"] == 1

    @patch("src.collectors.base.time.sleep")
    def test_circuit_breaker_records_success_on_retry_success(self, mock_sleep):
        cb = CircuitBreaker("test", failure_threshold=5)
        calls = [0]

        def flaky():
            calls[0] += 1
            if calls[0] == 1:
                raise ConnectionError("flaky")
            return make_feature_collection([], "test")

        c = _TestCollector(fetch_func=flaky, circuit_breaker=cb, max_retries=2)
        c.collect()
        stats = cb.get_stats()
        assert stats["total_successes"] == 1
        assert stats["total_failures"] == 0


# ==========================================================================
# DataAggregator + CircuitBreakerRegistry Integration
# ==========================================================================

ALL_DISABLED_CONFIG = {
    "enable_meshtastic": False,
    "enable_reticulum": False,
    "enable_hamclock": False,
    "enable_aredn": False,
}

ALL_ENABLED_CONFIG = {
    "enable_meshtastic": True,
    "enable_reticulum": True,
    "enable_hamclock": True,
    "enable_aredn": True,
    "cache_ttl_minutes": 15,
}


class TestAggregatorCircuitBreaker:
    """Tests for DataAggregator circuit breaker integration."""

    def test_aggregator_has_circuit_breaker_registry(self):
        agg = DataAggregator(ALL_DISABLED_CONFIG)
        assert isinstance(agg.circuit_breaker_registry, CircuitBreakerRegistry)

    def test_aggregator_creates_breakers_for_enabled_sources(self):
        agg = DataAggregator(ALL_ENABLED_CONFIG)
        states = agg.get_circuit_breaker_states()
        assert "meshtastic" in states
        assert "reticulum" in states
        assert "hamclock" in states
        assert "aredn" in states

    def test_collectors_have_circuit_breakers_assigned(self):
        agg = DataAggregator(ALL_ENABLED_CONFIG)
        for name, collector in agg._collectors.items():
            assert collector.circuit_breaker is not None
            assert collector.circuit_breaker.name == name

    def test_circuit_breaker_states_reflect_collector_health(self):
        agg = DataAggregator(ALL_ENABLED_CONFIG)
        # Simulate failures on meshtastic
        cb = agg.circuit_breaker_registry.get("meshtastic")
        for _ in range(5):
            cb.record_failure()

        states = agg.get_circuit_breaker_states()
        assert states["meshtastic"]["state"] == "open"
        assert states["reticulum"]["state"] == "closed"
