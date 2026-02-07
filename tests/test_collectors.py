"""Tests for individual data collectors."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from src.collectors.base import make_feature, make_feature_collection
from src.collectors.meshtastic_collector import MeshtasticCollector
from src.collectors.reticulum_collector import ReticulumCollector, RNS_NODE_TYPES
from src.collectors.hamclock_collector import HamClockCollector
from src.collectors.aredn_collector import AREDNCollector
from src.collectors.aggregator import DataAggregator


# ==========================================================================
# Meshtastic Collector
# ==========================================================================

class TestMeshtasticCollector:
    """Tests for MeshtasticCollector parsing and data flow."""

    def test_parse_api_node(self, sample_meshtastic_api_node):
        c = MeshtasticCollector()
        f = c._parse_api_node(sample_meshtastic_api_node)
        assert f is not None
        assert f["properties"]["id"] == "!a1b2c3d4"
        assert f["properties"]["name"] == "TestNode-Alpha"
        assert f["properties"]["hardware"] == "TBEAM"
        assert f["properties"]["network"] == "meshtastic"
        assert f["geometry"]["coordinates"] == [139.6917, 35.6895]
        assert f["properties"]["battery"] == 87
        assert f["properties"]["altitude"] == 40

    def test_parse_api_node_integer_coords(self, sample_meshtastic_api_node_integer_coords):
        c = MeshtasticCollector()
        f = c._parse_api_node(sample_meshtastic_api_node_integer_coords)
        assert f is not None
        lat = f["geometry"]["coordinates"][1]
        lon = f["geometry"]["coordinates"][0]
        assert abs(lat - 40.6892532) < 0.001
        assert abs(lon - (-74.0466305)) < 0.001

    def test_parse_api_node_no_position(self):
        c = MeshtasticCollector()
        node = {"num": 123, "user": {"id": "!abc"}}
        assert c._parse_api_node(node) is None

    def test_parse_api_node_invalid_coords(self):
        c = MeshtasticCollector()
        node = {
            "num": 123,
            "user": {"id": "!abc"},
            "position": {"latitude": 999, "longitude": 999},
        }
        assert c._parse_api_node(node) is None

    def test_parse_mqtt_node(self):
        c = MeshtasticCollector()
        f = c._parse_mqtt_node("!mqtt001", {
            "name": "MQTT-Node",
            "latitude": 51.5,
            "longitude": -0.12,
            "hardware": "TBEAM",
        })
        assert f is not None
        assert f["properties"]["id"] == "!mqtt001"
        assert f["properties"]["name"] == "MQTT-Node"
        assert f["geometry"]["coordinates"] == [-0.12, 51.5]

    def test_parse_mqtt_node_no_coords(self):
        c = MeshtasticCollector()
        assert c._parse_mqtt_node("!x", {"name": "NoCoords"}) is None

    def test_parse_mqtt_node_invalid_coords(self):
        c = MeshtasticCollector()
        assert c._parse_mqtt_node("!x", {
            "latitude": 200,
            "longitude": -0.1,
        }) is None

    @patch("src.collectors.meshtastic_collector.MQTT_CACHE_PATH")
    def test_fetch_from_mqtt_cache_dict(self, mock_path, sample_mqtt_cache_dict, tmp_path):
        cache_file = tmp_path / "mqtt_nodes.json"
        cache_file.write_text(json.dumps(sample_mqtt_cache_dict))
        # Replace the module-level Path with our real temp file
        mock_path.exists.return_value = True
        c = MeshtasticCollector()
        with patch("builtins.open", mock_open(read_data=json.dumps(sample_mqtt_cache_dict))):
            features = c._fetch_from_mqtt_cache()
        assert len(features) == 2

    @patch("src.collectors.meshtastic_collector.MQTT_CACHE_PATH")
    def test_fetch_from_mqtt_cache_geojson(self, mock_path, sample_mqtt_cache_geojson):
        mock_path.exists.return_value = True
        c = MeshtasticCollector()
        with patch("builtins.open", mock_open(read_data=json.dumps(sample_mqtt_cache_geojson))):
            features = c._fetch_from_mqtt_cache()
        assert len(features) == 1
        assert features[0]["properties"]["id"] == "!geo001"

    def test_online_detection(self, sample_meshtastic_api_node):
        c = MeshtasticCollector()
        # Recent lastHeard -> online
        sample_meshtastic_api_node["lastHeard"] = int(time.time()) - 60
        f = c._parse_api_node(sample_meshtastic_api_node)
        assert f["properties"]["is_online"] is True

        # Old lastHeard -> offline
        sample_meshtastic_api_node["lastHeard"] = int(time.time()) - 2000
        f = c._parse_api_node(sample_meshtastic_api_node)
        assert f["properties"]["is_online"] is False


# ==========================================================================
# Reticulum Collector
# ==========================================================================

class TestReticulumCollector:
    """Tests for ReticulumCollector parsing."""

    def test_parse_rns_interface(self, sample_rns_interface):
        c = ReticulumCollector()
        f = c._parse_rns_interface(sample_rns_interface)
        assert f is not None
        assert f["properties"]["id"] == "abc123def456"
        assert f["properties"]["name"] == "RNode-LoRa-900"
        assert f["properties"]["network"] == "reticulum"
        assert f["properties"]["node_type"] == "RNode (LoRa)"
        assert f["properties"]["is_online"] is True
        assert f["geometry"]["coordinates"] == [-118.2437, 34.0522]

    def test_parse_rns_interface_no_coords(self):
        c = ReticulumCollector()
        assert c._parse_rns_interface({"name": "NoCoords"}) is None

    def test_parse_rns_interface_invalid_coords(self):
        c = ReticulumCollector()
        assert c._parse_rns_interface({
            "name": "Bad",
            "latitude": 999,
            "longitude": 0,
        }) is None

    def test_rns_node_types_mapping(self):
        assert RNS_NODE_TYPES["rnode"] == "RNode (LoRa)"
        assert RNS_NODE_TYPES["nomadnet"] == "NomadNet"
        assert RNS_NODE_TYPES["tcp"] == "TCP Transport"

    @patch("src.collectors.reticulum_collector.RNS_CACHE_PATH")
    def test_read_cache_file_geojson(self, mock_path, tmp_path):
        cache_data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                    "properties": {"id": "rns1", "network": "reticulum"},
                }
            ],
        }
        c = ReticulumCollector()
        with patch("builtins.open", mock_open(read_data=json.dumps(cache_data))):
            mock_path.exists.return_value = True
            features = c._read_cache_file(mock_path)
        assert len(features) == 1

    @patch("subprocess.run")
    def test_fetch_from_rnstatus_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError
        c = ReticulumCollector()
        assert c._fetch_from_rnstatus() == []


# ==========================================================================
# HamClock Collector
# ==========================================================================

class TestHamClockCollector:
    """Tests for HamClockCollector space weather and terminator."""

    def test_assess_band_conditions_excellent(self):
        c = HamClockCollector()
        assert c._assess_band_conditions(170, 2) == "excellent"

    def test_assess_band_conditions_good(self):
        c = HamClockCollector()
        assert c._assess_band_conditions(120, 3) == "good"

    def test_assess_band_conditions_fair_sfi(self):
        c = HamClockCollector()
        assert c._assess_band_conditions(80, 3) == "fair"

    def test_assess_band_conditions_fair_kp(self):
        c = HamClockCollector()
        assert c._assess_band_conditions(150, 5) == "fair"

    def test_assess_band_conditions_poor_storm(self):
        c = HamClockCollector()
        assert c._assess_band_conditions(150, 7) == "poor"

    def test_assess_band_conditions_poor_low_sfi(self):
        c = HamClockCollector()
        assert c._assess_band_conditions(60, 3) == "poor"

    def test_assess_band_conditions_unknown(self):
        c = HamClockCollector()
        assert c._assess_band_conditions(None, None) == "unknown"
        assert c._assess_band_conditions("bad", 3) == "unknown"

    def test_solar_terminator_calculation(self):
        c = HamClockCollector()
        result = c._calculate_solar_terminator()
        assert "subsolar_lat" in result
        assert "subsolar_lon" in result
        assert "timestamp" in result
        assert -90 <= result["subsolar_lat"] <= 90
        assert -180 <= result["subsolar_lon"] <= 180


# ==========================================================================
# AREDN Collector
# ==========================================================================

class TestAREDNCollector:
    """Tests for AREDNCollector parsing."""

    def test_parse_sysinfo(self, sample_aredn_sysinfo):
        c = AREDNCollector()
        f = c._parse_sysinfo(sample_aredn_sysinfo, "KN6PLV-HAP")
        assert f is not None
        assert f["properties"]["id"] == "KN6PLV-HAP"
        assert f["properties"]["network"] == "aredn"
        assert f["properties"]["hardware"] == "MikroTik hAP ac lite"
        assert f["properties"]["firmware"] == "3.24.4.0"
        assert f["properties"]["is_online"] is True
        assert f["properties"]["grid_square"] == "DM04"

    def test_parse_sysinfo_no_coords(self):
        c = AREDNCollector()
        assert c._parse_sysinfo({"node": "X"}, "X") is None

    def test_parse_sysinfo_invalid_coords(self):
        c = AREDNCollector()
        assert c._parse_sysinfo({"node": "X", "lat": "abc", "lon": "def"}, "X") is None

    def test_parse_lqm_neighbor_returns_none(self):
        c = AREDNCollector()
        # Currently returns None (no coords in LQM)
        assert c._parse_lqm_neighbor({"name": "Neighbor1"}) is None
        assert c._parse_lqm_neighbor({}) is None


# ==========================================================================
# Data Aggregator
# ==========================================================================

class TestDataAggregator:
    """Tests for DataAggregator merging and deduplication."""

    def test_creates_collectors_from_config(self):
        config = dict(DEFAULT_CONFIG_SUBSET)
        agg = DataAggregator(config)
        assert "meshtastic" in agg._collectors
        assert "reticulum" in agg._collectors

    def test_disabled_source_not_created(self):
        config = dict(DEFAULT_CONFIG_SUBSET)
        config["enable_meshtastic"] = False
        agg = DataAggregator(config)
        assert "meshtastic" not in agg._collectors

    def test_collect_source_unknown(self):
        agg = DataAggregator({"enable_meshtastic": False, "enable_reticulum": False,
                              "enable_hamclock": False, "enable_aredn": False})
        result = agg.collect_source("nonexistent")
        assert result["type"] == "FeatureCollection"
        assert result["features"] == []

    def test_clear_all_caches(self):
        config = dict(DEFAULT_CONFIG_SUBSET)
        agg = DataAggregator(config)
        for c in agg._collectors.values():
            c._cache = {"test": True}
        agg.clear_all_caches()
        for c in agg._collectors.values():
            assert c._cache is None

    @patch.object(MeshtasticCollector, "collect")
    @patch.object(ReticulumCollector, "collect")
    @patch.object(HamClockCollector, "collect")
    @patch.object(AREDNCollector, "collect")
    def test_deduplication(self, mock_aredn, mock_ham, mock_ret, mock_mesh):
        mock_mesh.return_value = make_feature_collection(
            [make_feature("dup-1", 1.0, 2.0, "meshtastic")], "meshtastic"
        )
        mock_ret.return_value = make_feature_collection(
            [make_feature("dup-1", 1.0, 2.0, "reticulum")], "reticulum"
        )
        mock_ham.return_value = make_feature_collection([], "hamclock")
        mock_aredn.return_value = make_feature_collection([], "aredn")

        agg = DataAggregator(dict(DEFAULT_CONFIG_SUBSET))
        result = agg.collect_all()
        # dup-1 should only appear once
        ids = [f["properties"]["id"] for f in result["features"]]
        assert ids.count("dup-1") == 1

    @patch.object(MeshtasticCollector, "collect")
    @patch.object(ReticulumCollector, "collect")
    @patch.object(HamClockCollector, "collect")
    @patch.object(AREDNCollector, "collect")
    def test_source_counts(self, mock_aredn, mock_ham, mock_ret, mock_mesh):
        mock_mesh.return_value = make_feature_collection(
            [make_feature("m1", 1.0, 2.0, "meshtastic"),
             make_feature("m2", 3.0, 4.0, "meshtastic")],
            "meshtastic",
        )
        mock_ret.return_value = make_feature_collection(
            [make_feature("r1", 5.0, 6.0, "reticulum")], "reticulum"
        )
        mock_ham.return_value = make_feature_collection([], "hamclock")
        mock_aredn.return_value = make_feature_collection([], "aredn")

        agg = DataAggregator(dict(DEFAULT_CONFIG_SUBSET))
        result = agg.collect_all()
        assert result["properties"]["sources"]["meshtastic"] == 2
        assert result["properties"]["sources"]["reticulum"] == 1
        assert result["properties"]["total_nodes"] == 3


# Helper config for aggregator tests
DEFAULT_CONFIG_SUBSET = {
    "enable_meshtastic": True,
    "enable_reticulum": True,
    "enable_hamclock": True,
    "enable_aredn": True,
    "cache_ttl_minutes": 15,
}
