"""Tests for server-side topology GeoJSON with SNR edge coloring."""

import pytest

from src.collectors.mqtt_subscriber import (
    MQTTNodeStore,
    _classify_snr,
    SNR_TIERS,
    SNR_DEFAULT,
    SNR_UNKNOWN,
)


class TestSNRClassification:
    """Tests for _classify_snr() quality tier classification."""

    def test_excellent_snr(self):
        label, color = _classify_snr(10.0)
        assert label == "excellent"
        assert color == "#4caf50"

    def test_good_snr(self):
        label, color = _classify_snr(6.0)
        assert label == "good"
        assert color == "#8bc34a"

    def test_marginal_snr(self):
        label, color = _classify_snr(2.0)
        assert label == "marginal"
        assert color == "#ffeb3b"

    def test_poor_snr(self):
        label, color = _classify_snr(-5.0)
        assert label == "poor"
        assert color == "#ff9800"

    def test_bad_snr(self):
        label, color = _classify_snr(-15.0)
        assert label == "bad"
        assert color == "#f44336"

    def test_none_snr(self):
        label, color = _classify_snr(None)
        assert label == "unknown"
        assert color == "#9e9e9e"

    def test_invalid_snr_string(self):
        label, color = _classify_snr("not_a_number")
        assert label == "unknown"

    def test_boundary_excellent(self):
        # SNR > 8 is excellent, exactly 8 is good
        label, _ = _classify_snr(8.1)
        assert label == "excellent"
        label, _ = _classify_snr(8.0)
        assert label == "good"

    def test_boundary_good(self):
        # SNR > 5 is good, exactly 5 is marginal
        label, _ = _classify_snr(5.1)
        assert label == "good"
        label, _ = _classify_snr(5.0)
        assert label == "marginal"

    def test_boundary_marginal(self):
        # SNR > 0 is marginal, exactly 0 is poor
        label, _ = _classify_snr(0.1)
        assert label == "marginal"
        label, _ = _classify_snr(0.0)
        assert label == "poor"

    def test_boundary_poor(self):
        # SNR > -10 is poor, exactly -10 is bad
        label, _ = _classify_snr(-9.9)
        assert label == "poor"
        label, _ = _classify_snr(-10.0)
        assert label == "bad"


class TestTopologyGeoJSON:
    """Tests for MQTTNodeStore.get_topology_geojson()."""

    @pytest.fixture
    def store(self):
        return MQTTNodeStore()

    def test_empty_topology(self, store):
        result = store.get_topology_geojson()
        assert result["type"] == "FeatureCollection"
        assert result["features"] == []
        assert result["properties"]["link_count"] == 0

    def test_topology_with_links(self, store):
        # Add two nodes with positions
        store.update_position("!src", 35.0, 139.0)
        store.update_position("!tgt", 35.1, 139.1)
        # Add neighbor relationship
        store.update_neighbors("!src", [{"node_id": "!tgt", "snr": 9.5}])

        result = store.get_topology_geojson()
        assert result["type"] == "FeatureCollection"
        assert len(result["features"]) == 1
        assert result["properties"]["link_count"] == 1

        feature = result["features"][0]
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "LineString"

        coords = feature["geometry"]["coordinates"]
        assert len(coords) == 2
        assert coords[0] == [139.0, 35.0]  # source [lon, lat]
        assert coords[1] == [139.1, 35.1]  # target [lon, lat]

        props = feature["properties"]
        assert props["source"] == "!src"
        assert props["target"] == "!tgt"
        assert props["snr"] == 9.5
        assert props["quality"] == "excellent"
        assert props["color"] == "#4caf50"

    def test_topology_with_poor_snr(self, store):
        store.update_position("!a", 35.0, 139.0)
        store.update_position("!b", 35.1, 139.1)
        store.update_neighbors("!a", [{"node_id": "!b", "snr": -5.0}])

        result = store.get_topology_geojson()
        feature = result["features"][0]
        assert feature["properties"]["quality"] == "poor"
        assert feature["properties"]["color"] == "#ff9800"

    def test_topology_with_no_snr(self, store):
        store.update_position("!a", 35.0, 139.0)
        store.update_position("!b", 35.1, 139.1)
        store.update_neighbors("!a", [{"node_id": "!b", "snr": None}])

        result = store.get_topology_geojson()
        feature = result["features"][0]
        assert feature["properties"]["quality"] == "unknown"
        assert feature["properties"]["color"] == "#9e9e9e"

    def test_topology_skips_unpositioned_nodes(self, store):
        store.update_position("!src", 35.0, 139.0)
        # !tgt has no position
        store.update_neighbors("!src", [{"node_id": "!tgt", "snr": 5.0}])

        result = store.get_topology_geojson()
        assert len(result["features"]) == 0

    def test_topology_multiple_links(self, store):
        store.update_position("!a", 35.0, 139.0)
        store.update_position("!b", 35.1, 139.1)
        store.update_position("!c", 35.2, 139.2)
        store.update_neighbors("!a", [
            {"node_id": "!b", "snr": 10.0},
            {"node_id": "!c", "snr": -15.0},
        ])

        result = store.get_topology_geojson()
        assert len(result["features"]) == 2
        qualities = [f["properties"]["quality"] for f in result["features"]]
        assert "excellent" in qualities
        assert "bad" in qualities
