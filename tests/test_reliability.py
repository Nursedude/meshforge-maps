"""Tests for reliability integration: retry with backoff in collectors."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.base import BaseCollector, make_feature, make_feature_collection
from src.collectors.aggregator import DataAggregator


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


# ==========================================================================
# DataAggregator basic integration
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


class TestAggregatorBasics:
    """Tests for DataAggregator without circuit breaker."""

    def test_aggregator_has_event_bus(self):
        agg = DataAggregator(ALL_DISABLED_CONFIG)
        assert agg.event_bus is not None

    def test_aggregator_has_perf_monitor(self):
        agg = DataAggregator(ALL_DISABLED_CONFIG)
        assert agg.perf_monitor is not None

    def test_aggregator_source_health(self):
        agg = DataAggregator(ALL_DISABLED_CONFIG)
        health = agg.get_source_health()
        assert isinstance(health, dict)
