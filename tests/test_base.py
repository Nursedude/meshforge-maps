"""Tests for base collector module: GeoJSON helpers and BaseCollector caching."""

import time


from src.collectors.base import BaseCollector, make_feature, make_feature_collection, point_in_bboxes


class TestPointInBboxes:
    def test_none_bboxes_pass_through(self):
        assert point_in_bboxes(20, -157, None) is True
        assert point_in_bboxes(20, -157, []) is True

    def test_normal_bbox_hit_and_miss(self):
        hawaii = [[18.5, -161, 22.5, -154]]
        assert point_in_bboxes(20, -157, hawaii) is True
        assert point_in_bboxes(40, -100, hawaii) is False

    def test_antimeridian_crossing_bbox(self):
        # west=170, east=-150: crosses the antimeridian
        wrap = [[15, 170, 25, -150]]
        assert point_in_bboxes(20, 175, wrap) is True   # east of 170
        assert point_in_bboxes(20, -160, wrap) is True  # west of -150
        assert point_in_bboxes(20, 180, wrap) is True
        assert point_in_bboxes(20, 0, wrap) is False
        assert point_in_bboxes(5, 175, wrap) is False   # lat out of range

    def test_invalid_coords_rejected(self):
        assert point_in_bboxes("abc", 0, [[0, 0, 1, 1]]) is False
        assert point_in_bboxes(0, None, [[0, 0, 1, 1]]) is False


class TestPointInPolygon:
    # A unit square polygon as [lon, lat] pairs (open ring — last point != first).
    square = [[0, 0], [1, 0], [1, 1], [0, 1]]

    def test_inside_square(self):
        from src.collectors.base import point_in_polygon
        assert point_in_polygon(0.5, 0.5, self.square) is True

    def test_outside_square(self):
        from src.collectors.base import point_in_polygon
        assert point_in_polygon(1.5, 0.5, self.square) is False
        assert point_in_polygon(-0.1, 0.5, self.square) is False

    def test_closed_ring_equivalent_to_open(self):
        from src.collectors.base import point_in_polygon
        closed = self.square + [self.square[0]]
        assert point_in_polygon(0.5, 0.5, closed) is True
        assert point_in_polygon(2.0, 2.0, closed) is False

    def test_degenerate_polygon(self):
        from src.collectors.base import point_in_polygon
        assert point_in_polygon(0.5, 0.5, []) is False
        assert point_in_polygon(0.5, 0.5, [[0, 0], [1, 1]]) is False  # only 2 verts

    def test_invalid_coords(self):
        from src.collectors.base import point_in_polygon
        assert point_in_polygon("x", 0, self.square) is False


class TestPointInRegion:
    def test_both_empty_is_passthrough(self):
        from src.collectors.base import point_in_region
        assert point_in_region(0, 0, None, None) is True
        assert point_in_region(0, 0, [], []) is True

    def test_bbox_only(self):
        from src.collectors.base import point_in_region
        assert point_in_region(0.5, 0.5, [[0, 0, 1, 1]], None) is True
        assert point_in_region(2, 2, [[0, 0, 1, 1]], None) is False

    def test_polygon_only(self):
        from src.collectors.base import point_in_region
        poly = [[[0, 0], [1, 0], [1, 1], [0, 1]]]
        assert point_in_region(0.5, 0.5, None, poly) is True
        assert point_in_region(2, 2, None, poly) is False

    def test_either_matches(self):
        from src.collectors.base import point_in_region
        bboxes = [[10, 10, 11, 11]]
        polys = [[[0, 0], [1, 0], [1, 1], [0, 1]]]
        # inside polygon, outside bbox
        assert point_in_region(0.5, 0.5, bboxes, polys) is True
        # inside bbox, outside polygon
        assert point_in_region(10.5, 10.5, bboxes, polys) is True
        # outside both
        assert point_in_region(50, 50, bboxes, polys) is False


class TestUsConusPolygon:
    """Validate the shipped US CONUS polygon against a city table.

    Any failure here means the US region preset leaks or over-filters.
    Adjust config.US_CONUS_POLYGON vertices to make this table pass.
    """

    CITIES = [
        # (name, lat, lon, expected_inside)
        ("Seattle",        47.60, -122.30, True),
        ("Miami",          25.80,  -80.20, True),
        ("Key West",       24.55,  -81.80, True),
        ("Brownsville",    25.90,  -97.50, True),
        ("El Paso",        31.80, -106.45, True),
        ("San Diego",      32.72, -117.16, True),
        ("Bangor ME",      44.80,  -68.80, True),
        ("Chicago",        41.88,  -87.63, True),
        ("Denver",         39.74, -104.99, True),
        ("NYC",            40.71,  -74.00, True),
        ("Toronto",        43.70,  -79.40, False),
        ("Montreal",       45.50,  -73.60, False),
        ("Windsor ON",     42.30,  -83.00, False),
        ("Tijuana",        32.53, -117.00, False),
        ("Juarez",         31.70, -106.40, False),
        ("Nassau",         25.00,  -77.40, False),
        ("Havana",         23.10,  -82.40, False),
        ("Vancouver",      49.28, -123.12, False),
    ]

    def test_city_table(self):
        from src.collectors.base import point_in_polygon
        from src.utils.config import US_CONUS_POLYGON
        failures = []
        for name, lat, lon, expected in self.CITIES:
            actual = point_in_polygon(lat, lon, US_CONUS_POLYGON)
            if actual != expected:
                failures.append(f"{name} ({lat},{lon}): expected {expected}, got {actual}")
        assert not failures, "US polygon mismatches:\n  " + "\n  ".join(failures)


class TestUsPresetShape:
    def test_us_preset_has_polygon_and_island_bboxes(self):
        from src.utils.config import REGION_PRESETS, US_CONUS_POLYGON
        us = REGION_PRESETS["us"]
        # Islands remain as bboxes (Alaska, Hawaii, PR)
        assert isinstance(us["bbox"], list) and len(us["bbox"]) == 3
        # CONUS is now a polygon
        assert us["polygons"] == [US_CONUS_POLYGON]


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
        f = make_feature("n1", 1.0, 1.0, "reticulum")
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

    def test_null_island_rejected(self):
        """Coordinates near (0,0) are rejected as GPS artifacts."""
        f = make_feature("n1", 0.0, 0.0, "meshtastic")
        assert f is None
        f2 = make_feature("n2", 0.005, 0.005, "meshtastic")
        assert f2 is None

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

    def test_health_info_initial(self):
        c = ConcreteCollector()
        info = c.health_info
        assert info["source"] == "test"
        assert info["total_collections"] == 0
        assert info["total_errors"] == 0
        assert info["has_cache"] is False
        assert "last_error" not in info

    def test_health_info_after_success(self):
        c = ConcreteCollector()
        c.collect()
        info = c.health_info
        assert info["total_collections"] == 1
        assert info["total_errors"] == 0
        assert info["has_cache"] is True
        assert "last_success_age_seconds" in info
        assert info["last_success_age_seconds"] >= 0

    def test_health_info_after_error(self):
        calls = [0]
        def fetch():
            calls[0] += 1
            raise ConnectionError("test failure")
        c = ConcreteCollector(fetch_func=fetch)
        c.collect()  # Will fail and return empty
        info = c.health_info
        assert info["total_collections"] == 0
        assert info["total_errors"] == 1
        assert info["last_error"] == "test failure"
        assert "last_error_age_seconds" in info


class TestDeduplicateFeatures:
    """Direct unit tests for deduplicate_features()."""

    def test_removes_duplicates_first_wins(self):
        from src.collectors.base import deduplicate_features
        f1 = {"properties": {"id": "node1", "name": "First"}}
        f2 = {"properties": {"id": "node1", "name": "Second"}}
        result = deduplicate_features([[f1], [f2]])
        assert len(result) == 1
        assert result[0]["properties"]["name"] == "First"

    def test_distinct_ids_preserved(self):
        from src.collectors.base import deduplicate_features
        f1 = {"properties": {"id": "node1"}}
        f2 = {"properties": {"id": "node2"}}
        f3 = {"properties": {"id": "node3"}}
        result = deduplicate_features([[f1, f2], [f3]])
        assert len(result) == 3

    def test_no_id_included_when_allowed(self):
        from src.collectors.base import deduplicate_features
        f_with_id = {"properties": {"id": "node1"}}
        f_no_id = {"properties": {"name": "anonymous"}}
        result = deduplicate_features([[f_with_id, f_no_id]], allow_no_id=True)
        assert len(result) == 2

    def test_no_id_excluded_when_disallowed(self):
        from src.collectors.base import deduplicate_features
        f_with_id = {"properties": {"id": "node1"}}
        f_no_id = {"properties": {"name": "anonymous"}}
        result = deduplicate_features([[f_with_id, f_no_id]], allow_no_id=False)
        assert len(result) == 1

    def test_none_features_skipped(self):
        from src.collectors.base import deduplicate_features
        result = deduplicate_features([[None, {"properties": {"id": "x"}}]])
        assert len(result) == 1

    def test_empty_lists(self):
        from src.collectors.base import deduplicate_features
        result = deduplicate_features([[], []])
        assert result == []


class TestIsNodeOnline:
    """Regression tests for is_node_online clock-skew / unknown-network guards."""

    def test_recent_timestamp_online(self):
        from src.collectors.base import is_node_online
        assert is_node_online(time.time() - 10, "meshtastic") is True

    def test_old_timestamp_offline(self):
        from src.collectors.base import is_node_online
        assert is_node_online(time.time() - 10_000, "meshtastic") is False

    def test_future_timestamp_not_online(self):
        # Guards against a hostile broker forging last_heard far in the
        # future to pin nodes "online" indefinitely.
        from src.collectors.base import is_node_online
        assert is_node_online(time.time() + 10_000, "mqtt") is False

    def test_zero_or_missing_returns_none(self):
        from src.collectors.base import is_node_online
        assert is_node_online(0, "mqtt") is None
        assert is_node_online(None, "mqtt") is None
        assert is_node_online("", "mqtt") is None

    def test_unknown_network_returns_none(self):
        from src.collectors.base import is_node_online
        assert is_node_online(time.time(), "not-a-network") is None

    def test_empty_network_uses_default_threshold(self):
        from src.collectors.base import is_node_online
        assert is_node_online(time.time() - 10, "") is True

    def test_invalid_type_returns_none(self):
        from src.collectors.base import is_node_online
        assert is_node_online("abc", "mqtt") is None


class TestBoundedRead:
    """bounded_read enforces a max-bytes cap for third-party HTTP bodies."""

    def test_within_cap_returns_full_body(self):
        from io import BytesIO
        from src.collectors.base import bounded_read
        resp = BytesIO(b"x" * 100)
        assert bounded_read(resp, max_bytes=1024) == b"x" * 100

    def test_over_cap_raises(self):
        import pytest
        from io import BytesIO
        from src.collectors.base import bounded_read
        resp = BytesIO(b"x" * 1025)
        with pytest.raises(ValueError, match="exceeded"):
            bounded_read(resp, max_bytes=1024)
