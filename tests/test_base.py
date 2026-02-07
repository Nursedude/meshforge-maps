"""Tests for base collector module: GeoJSON helpers and BaseCollector caching."""

import time
from unittest.mock import patch

import pytest

from src.collectors.base import BaseCollector, make_feature, make_feature_collection


class TestMakeFeature:
    """Tests for make_feature() GeoJSON helper."""

    def test_basic_feature(self):
        f = make_feature("node1", 35.0, 139.0, "meshtastic", name="Test")
        assert f["type"] == "Feature"
        assert f["geometry"]["type"] == "Point"
        assert f["geometry"]["coordinates"] == [139.0, 35.0]
        assert f["properties"]["id"] == "node1"
        assert f["properties"]["name"] == "Test"
        assert f["properties"]["network"] == "meshtastic"

    def test_strips_none_values(self):
        f = make_feature("n1", 0.0, 0.0, "reticulum")
        props = f["properties"]
        # None-valued optional fields should be stripped
        assert "battery" not in props
        assert "snr" not in props
        assert "hardware" not in props

    def test_extra_properties(self):
        f = make_feature(
            "n1", 10.0, 20.0, "aredn",
            firmware="3.24.4.0",
            uptime="3 days",
        )
        assert f["properties"]["firmware"] == "3.24.4.0"
        assert f["properties"]["uptime"] == "3 days"

    def test_online_status(self):
        f = make_feature("n1", 10.0, 20.0, "meshtastic", is_online=True)
        assert f["properties"]["is_online"] is True

    def test_name_defaults_to_id(self):
        f = make_feature("my-node-id", 10.0, 20.0, "meshtastic")
        assert f["properties"]["name"] == "my-node-id"

    def test_all_standard_props(self):
        f = make_feature(
            "n1", 1.0, 2.0, "meshtastic",
            name="Full",
            node_type="router",
            is_online=True,
            last_seen=1700000000,
            hardware="TBEAM",
            role="ROUTER",
            battery=95,
            snr=12.5,
            rssi=-80,
            altitude=100,
            description="A test node",
        )
        p = f["properties"]
        assert p["battery"] == 95
        assert p["snr"] == 12.5
        assert p["rssi"] == -80
        assert p["altitude"] == 100
        assert p["description"] == "A test node"
        assert p["role"] == "ROUTER"


class TestMakeFeatureCollection:
    """Tests for make_feature_collection() GeoJSON wrapper."""

    def test_empty_collection(self):
        fc = make_feature_collection([], "test_source")
        assert fc["type"] == "FeatureCollection"
        assert fc["features"] == []
        assert fc["properties"]["source"] == "test_source"
        assert fc["properties"]["node_count"] == 0

    def test_with_features(self):
        features = [
            make_feature("n1", 1.0, 2.0, "meshtastic"),
            make_feature("n2", 3.0, 4.0, "reticulum"),
        ]
        fc = make_feature_collection(features, "aggregated")
        assert fc["properties"]["node_count"] == 2
        assert len(fc["features"]) == 2

    def test_collected_at_auto(self):
        fc = make_feature_collection([], "src")
        assert "collected_at" in fc["properties"]
        assert fc["properties"]["collected_at"].endswith("Z")

    def test_collected_at_override(self):
        fc = make_feature_collection([], "src", collected_at="2025-01-01T00:00:00Z")
        assert fc["properties"]["collected_at"] == "2025-01-01T00:00:00Z"


class ConcreteCollector(BaseCollector):
    """Concrete implementation for testing BaseCollector."""

    source_name = "test"

    def __init__(self, fetch_func=None, cache_ttl_seconds=900):
        super().__init__(cache_ttl_seconds)
        self._fetch_func = fetch_func or (lambda: make_feature_collection([], "test"))

    def _fetch(self):
        return self._fetch_func()


class TestBaseCollector:
    """Tests for BaseCollector caching behavior."""

    def test_first_collect_calls_fetch(self):
        called = []
        def fetch():
            called.append(1)
            return make_feature_collection([make_feature("n1", 1.0, 2.0, "test")], "test")

        c = ConcreteCollector(fetch_func=fetch)
        result = c.collect()
        assert len(called) == 1
        assert result["properties"]["node_count"] == 1

    def test_cache_hit(self):
        call_count = []
        def fetch():
            call_count.append(1)
            return make_feature_collection([], "test")

        c = ConcreteCollector(fetch_func=fetch, cache_ttl_seconds=300)
        c.collect()
        c.collect()
        # Second call should use cache
        assert len(call_count) == 1

    def test_cache_expired(self):
        call_count = []
        def fetch():
            call_count.append(1)
            return make_feature_collection([], "test")

        c = ConcreteCollector(fetch_func=fetch, cache_ttl_seconds=1)
        c.collect()
        time.sleep(1.1)
        c.collect()
        assert len(call_count) == 2

    def test_stale_cache_on_error(self):
        calls = [0]
        def fetch():
            calls[0] += 1
            if calls[0] == 1:
                return make_feature_collection([make_feature("n1", 1.0, 2.0, "test")], "test")
            raise ConnectionError("down")

        c = ConcreteCollector(fetch_func=fetch, cache_ttl_seconds=0)
        first = c.collect()
        assert first["properties"]["node_count"] == 1
        # Force cache expiry
        c._cache_time = 0
        second = c.collect()
        # Should return stale cache
        assert second["properties"]["node_count"] == 1

    def test_empty_on_error_no_cache(self):
        def fetch():
            raise ConnectionError("down")

        c = ConcreteCollector(fetch_func=fetch)
        result = c.collect()
        assert result["properties"]["node_count"] == 0

    def test_clear_cache(self):
        c = ConcreteCollector()
        c.collect()
        assert c._cache is not None
        c.clear_cache()
        assert c._cache is None
        assert c._cache_time == 0
