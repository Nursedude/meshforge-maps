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

    @patch("src.collectors.reticulum_collector.RNS_CACHE_PATH")
    def test_read_cache_file_dict_filters_invalid_coords(self, mock_path):
        """Regression test: _read_cache_file must not append None from make_feature."""
        cache_data = {
            "valid_node": {
                "name": "Good",
                "latitude": 34.0,
                "longitude": -118.0,
                "type": "rnode",
            },
            "bad_node": {
                "name": "Bad",
                "latitude": 999,
                "longitude": 999,
                "type": "tcp",
            },
        }
        c = ReticulumCollector()
        mock_path.exists.return_value = True
        with patch("builtins.open", mock_open(read_data=json.dumps(cache_data))):
            features = c._read_cache_file(mock_path)
        # bad_node should be filtered out (invalid coords), not produce None
        assert len(features) == 1
        assert None not in features
        assert features[0]["properties"]["name"] == "Good"

    @patch("subprocess.run")
    def test_fetch_from_rnstatus_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError
        c = ReticulumCollector()
        assert c._fetch_from_rnstatus() == []


# ==========================================================================
# HamClock Collector
# ==========================================================================

class TestHamClockCollector:
    """Tests for HamClockCollector API-only architecture."""

    # --- Band condition assessment (unchanged logic) ---

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

    # --- Constructor / config ---

    def test_constructor_defaults(self):
        c = HamClockCollector()
        assert c._hamclock_host == "localhost"
        assert c._hamclock_port == 8080
        assert c._hamclock_api == "http://localhost:8080"

    def test_constructor_custom_host_port(self):
        c = HamClockCollector(hamclock_host="192.168.1.50", hamclock_port=8082)
        assert c._hamclock_api == "http://192.168.1.50:8082"
        assert c._hamclock_host == "192.168.1.50"
        assert c._hamclock_port == 8082

    # --- Key=value parser ---

    def test_parse_key_value(self):
        from src.collectors.hamclock_collector import _parse_key_value
        raw = "SFI=156\nKp=2\nA=5\nXray=B1.2\n"
        result = _parse_key_value(raw)
        assert result["SFI"] == "156"
        assert result["Kp"] == "2"
        assert result["A"] == "5"
        assert result["Xray"] == "B1.2"

    def test_parse_key_value_empty(self):
        from src.collectors.hamclock_collector import _parse_key_value
        assert _parse_key_value("") == {}
        assert _parse_key_value("no equals here") == {}

    def test_parse_key_value_with_equals_in_value(self):
        from src.collectors.hamclock_collector import _parse_key_value
        result = _parse_key_value("path=DE to DX = 5000km")
        assert result["path"] == "DE to DX = 5000km"

    # --- HamClock availability ---

    @patch.object(HamClockCollector, "_fetch_text")
    def test_is_hamclock_available_true(self, mock_fetch):
        mock_fetch.return_value = "Version=4.21\nUptime=12345"
        c = HamClockCollector()
        assert c.is_hamclock_available() is True
        assert c._hamclock_available is True

    @patch.object(HamClockCollector, "_fetch_text")
    def test_is_hamclock_available_false(self, mock_fetch):
        mock_fetch.return_value = None
        c = HamClockCollector()
        assert c.is_hamclock_available() is False
        assert c._hamclock_available is False

    # --- OpenHamClock auto-detection ---

    def test_constructor_openhamclock_defaults(self):
        c = HamClockCollector()
        assert c._openhamclock_port == 3000
        assert c._detected_variant is None

    def test_constructor_custom_openhamclock_port(self):
        c = HamClockCollector(openhamclock_port=3001)
        assert c._openhamclock_port == 3001

    @patch.object(HamClockCollector, "_fetch_text")
    def test_openhamclock_fallback_when_hamclock_down(self, mock_fetch):
        """When HamClock port 8080 fails, should try OpenHamClock port 3000."""
        # First call (port 8080) returns None, second call (port 3000) succeeds
        mock_fetch.side_effect = [None, "Version=1.0\nUptime=100"]
        c = HamClockCollector()
        assert c.is_hamclock_available() is True
        assert c._detected_variant == "openhamclock"
        assert c._hamclock_api == "http://localhost:3000"
        assert mock_fetch.call_count == 2

    @patch.object(HamClockCollector, "_fetch_text")
    def test_hamclock_primary_preferred_over_openhamclock(self, mock_fetch):
        """When HamClock port 8080 works, don't try OpenHamClock."""
        mock_fetch.return_value = "Version=4.21\nUptime=12345"
        c = HamClockCollector()
        assert c.is_hamclock_available() is True
        assert c._detected_variant == "hamclock"
        assert c._hamclock_api == "http://localhost:8080"
        # Should only call once (primary port succeeded)
        assert mock_fetch.call_count == 1

    @patch.object(HamClockCollector, "_fetch_text")
    def test_both_ports_down(self, mock_fetch):
        """When both HamClock and OpenHamClock are down, returns False."""
        mock_fetch.return_value = None
        c = HamClockCollector()
        assert c.is_hamclock_available() is False
        assert c._detected_variant is None
        # Should try both ports
        assert mock_fetch.call_count == 2

    @patch.object(HamClockCollector, "_fetch_text")
    def test_same_port_skips_fallback(self, mock_fetch):
        """When openhamclock_port == hamclock_port, don't double-check."""
        mock_fetch.return_value = None
        c = HamClockCollector(hamclock_port=3000, openhamclock_port=3000)
        assert c.is_hamclock_available() is False
        assert mock_fetch.call_count == 1  # Only tried once

    @patch.object(HamClockCollector, "is_hamclock_available", return_value=True)
    @patch.object(HamClockCollector, "_fetch_space_weather_hamclock")
    @patch.object(HamClockCollector, "_fetch_band_conditions_hamclock")
    @patch.object(HamClockCollector, "_fetch_voacap")
    @patch.object(HamClockCollector, "_fetch_de")
    @patch.object(HamClockCollector, "_fetch_dx")
    @patch.object(HamClockCollector, "_fetch_dxspots")
    def test_fetch_reports_openhamclock_variant(
        self, mock_spots, mock_dx, mock_de, mock_voacap, mock_bc, mock_wx, mock_avail
    ):
        mock_wx.return_value = {"source": "OpenHamClock API"}
        mock_bc.return_value = None
        mock_voacap.return_value = None
        mock_de.return_value = None
        mock_dx.return_value = None
        mock_spots.return_value = None

        c = HamClockCollector()
        c._detected_variant = "openhamclock"
        c._openhamclock_port = 3000
        fc = c._fetch()

        assert fc["properties"]["hamclock"]["source"] == "OpenHamClock API"
        assert fc["properties"]["hamclock"]["variant"] == "openhamclock"
        assert fc["properties"]["hamclock"]["port"] == 3000

    # --- HamClock API space weather ---

    @patch.object(HamClockCollector, "_fetch_text")
    def test_fetch_space_weather_hamclock(self, mock_fetch):
        mock_fetch.return_value = "SFI=156\nKp=2\nA=5\nXray=B1.2\nSSN=120\n"
        c = HamClockCollector()
        result = c._fetch_space_weather_hamclock()
        assert result["source"] == "HamClock API"
        assert result["solar_flux"] == "156"
        assert result["kp_index"] == "2"
        assert result["a_index"] == "5"
        assert result["xray_flux"] == "B1.2"
        assert result["ssn"] == "120"
        assert result["band_conditions"] == "excellent"  # SFI 156, Kp 2

    @patch.object(HamClockCollector, "_fetch_text")
    def test_fetch_space_weather_hamclock_unavailable(self, mock_fetch):
        mock_fetch.return_value = None
        c = HamClockCollector()
        result = c._fetch_space_weather_hamclock()
        assert result["source"] == "HamClock API"
        assert "solar_flux" not in result

    # --- VOACAP ---

    @patch.object(HamClockCollector, "_fetch_text")
    def test_fetch_voacap_parses_bands(self, mock_fetch):
        mock_fetch.return_value = "path=DE to DX\nutc=14\n80m=23,12\n40m=65,18\n20m=90,25\n"
        c = HamClockCollector()
        result = c._fetch_voacap()
        assert result is not None
        assert result["path"] == "DE to DX"
        assert result["utc"] == "14"
        assert result["bands"]["80m"]["reliability"] == 23
        assert result["bands"]["80m"]["snr"] == 12
        assert result["bands"]["80m"]["status"] == "poor"
        assert result["bands"]["20m"]["reliability"] == 90
        assert result["bands"]["20m"]["status"] == "excellent"
        assert result["best_band"] == "20m"
        assert result["best_reliability"] == 90

    @patch.object(HamClockCollector, "_fetch_text")
    def test_fetch_voacap_returns_none_when_no_bands(self, mock_fetch):
        mock_fetch.return_value = "path=DE to DX\nutc=14\n"
        c = HamClockCollector()
        assert c._fetch_voacap() is None

    @patch.object(HamClockCollector, "_fetch_text")
    def test_fetch_voacap_returns_none_when_unavailable(self, mock_fetch):
        mock_fetch.return_value = None
        c = HamClockCollector()
        assert c._fetch_voacap() is None

    # --- Band conditions from HamClock ---

    @patch.object(HamClockCollector, "_fetch_text")
    def test_fetch_band_conditions_hamclock(self, mock_fetch):
        mock_fetch.return_value = "80m-40m=Good\n30m-20m=Fair\n17m-15m=Poor\n12m-10m=Poor\n"
        c = HamClockCollector()
        result = c._fetch_band_conditions_hamclock()
        assert result is not None
        assert result["bands"]["80m-40m"] == "Good"
        assert result["bands"]["30m-20m"] == "Fair"

    # --- DE/DX location ---

    @patch.object(HamClockCollector, "_fetch_text")
    def test_fetch_de(self, mock_fetch):
        mock_fetch.return_value = "lat=21.31\nlng=-157.86\ngrid=BL11\ncall=WH6GXZ\n"
        c = HamClockCollector()
        result = c._fetch_de()
        assert result is not None
        assert result["lat"] == "21.31"
        assert result["lon"] == "-157.86"
        assert result["grid"] == "BL11"
        assert result["call"] == "WH6GXZ"

    @patch.object(HamClockCollector, "_fetch_text")
    def test_fetch_dx(self, mock_fetch):
        mock_fetch.return_value = "lat=48.85\nlng=2.35\ngrid=JN18\ncall=F5ABC\n"
        c = HamClockCollector()
        result = c._fetch_dx()
        assert result["lat"] == "48.85"
        assert result["grid"] == "JN18"

    # --- DX Spots ---

    @patch.object(HamClockCollector, "_fetch_text")
    def test_fetch_dxspots_parses_spots(self, mock_fetch):
        mock_fetch.return_value = "Spot0=JA1ABC 14250 W6XYZ 1430 CQ\nSpot1=VK3DEF 7015 K1ZZ 1432\n"
        c = HamClockCollector()
        result = c._fetch_dxspots()
        assert result is not None
        assert len(result) == 2
        assert result[0]["dx_call"] == "JA1ABC"
        assert result[0]["freq_khz"] == "14250"
        assert result[0]["de_call"] == "W6XYZ"
        assert result[0]["utc"] == "1430"
        assert result[0]["comment"] == "CQ"
        assert result[1]["dx_call"] == "VK3DEF"
        assert result[1]["freq_khz"] == "7015"

    @patch.object(HamClockCollector, "_fetch_text")
    def test_fetch_dxspots_returns_none_when_empty(self, mock_fetch):
        mock_fetch.return_value = "count=0\n"
        c = HamClockCollector()
        assert c._fetch_dxspots() is None

    @patch.object(HamClockCollector, "_fetch_text")
    def test_fetch_dxspots_returns_none_when_unavailable(self, mock_fetch):
        mock_fetch.return_value = None
        c = HamClockCollector()
        assert c._fetch_dxspots() is None

    @patch.object(HamClockCollector, "_fetch_text")
    def test_fetch_dxspots_handles_short_lines(self, mock_fetch):
        mock_fetch.return_value = "Spot0=JA1ABC 14250 W6XYZ\n"
        c = HamClockCollector()
        result = c._fetch_dxspots()
        assert result is not None
        assert len(result) == 1
        assert result[0]["dx_call"] == "JA1ABC"
        assert "utc" not in result[0]

    # --- get_hamclock_data ---

    @patch.object(HamClockCollector, "is_hamclock_available", return_value=True)
    @patch.object(HamClockCollector, "_fetch_space_weather_hamclock")
    @patch.object(HamClockCollector, "_fetch_band_conditions_hamclock")
    @patch.object(HamClockCollector, "_fetch_voacap")
    @patch.object(HamClockCollector, "_fetch_de")
    @patch.object(HamClockCollector, "_fetch_dx")
    @patch.object(HamClockCollector, "_fetch_dxspots")
    def test_get_hamclock_data_returns_all(
        self, mock_spots, mock_dx, mock_de, mock_voacap, mock_bc, mock_wx, mock_avail
    ):
        mock_wx.return_value = {"source": "HamClock API", "solar_flux": "150", "band_conditions": "good"}
        mock_bc.return_value = {"bands": {"80m-40m": "Good"}}
        mock_voacap.return_value = {"bands": {"20m": {"reliability": 90}}}
        mock_de.return_value = {"call": "WH6GXZ", "grid": "BL11"}
        mock_dx.return_value = {"call": "F5ABC", "grid": "JN18"}
        mock_spots.return_value = [{"dx_call": "JA1ABC", "freq_khz": "14250"}]

        c = HamClockCollector()
        data = c.get_hamclock_data()
        assert data["available"] is True
        assert data["source"] == "HamClock API"
        assert data["space_weather"]["solar_flux"] == "150"
        assert data["band_conditions"]["bands"]["80m-40m"] == "Good"
        assert data["voacap"]["bands"]["20m"]["reliability"] == 90
        assert data["de_station"]["call"] == "WH6GXZ"
        assert data["dx_station"]["call"] == "F5ABC"
        assert data["dxspots"][0]["dx_call"] == "JA1ABC"

    # --- Full _fetch: HamClock up ---

    @patch.object(HamClockCollector, "is_hamclock_available", return_value=True)
    @patch.object(HamClockCollector, "_fetch_space_weather_hamclock")
    @patch.object(HamClockCollector, "_fetch_band_conditions_hamclock")
    @patch.object(HamClockCollector, "_fetch_voacap")
    @patch.object(HamClockCollector, "_fetch_de")
    @patch.object(HamClockCollector, "_fetch_dx")
    @patch.object(HamClockCollector, "_fetch_dxspots")
    def test_fetch_uses_hamclock_when_available(
        self, mock_spots, mock_dx, mock_de, mock_voacap, mock_bc, mock_wx, mock_avail
    ):
        mock_wx.return_value = {"source": "HamClock API", "solar_flux": "150", "band_conditions": "excellent"}
        mock_bc.return_value = {"bands": {"80m-40m": "Good"}}
        mock_voacap.return_value = {"bands": {"20m": {"reliability": 90}}}
        mock_de.return_value = {"call": "WH6GXZ"}
        mock_dx.return_value = {"call": "F5ABC"}
        mock_spots.return_value = [{"dx_call": "JA1ABC", "freq_khz": "14250"}]

        c = HamClockCollector()
        fc = c._fetch()

        assert fc["properties"]["space_weather"]["source"] == "HamClock API"
        assert fc["properties"]["hamclock"]["available"] is True
        assert fc["properties"]["hamclock"]["source"] == "HamClock API"
        assert fc["properties"]["hamclock"]["band_conditions"]["bands"]["80m-40m"] == "Good"
        assert fc["properties"]["hamclock"]["voacap"]["bands"]["20m"]["reliability"] == 90
        assert fc["properties"]["hamclock"]["de_station"]["call"] == "WH6GXZ"
        assert fc["properties"]["hamclock"]["dx_station"]["call"] == "F5ABC"
        assert fc["properties"]["hamclock"]["dxspots"][0]["dx_call"] == "JA1ABC"
        assert "solar_terminator" in fc["properties"]

    # --- Full _fetch: HamClock down (NOAA fallback) ---

    @patch.object(HamClockCollector, "is_hamclock_available", return_value=False)
    @patch.object(HamClockCollector, "_fetch_space_weather_noaa")
    def test_fetch_falls_back_to_noaa(self, mock_noaa, mock_avail):
        mock_noaa.return_value = {"source": "NOAA SWPC", "solar_flux": "120", "band_conditions": "good"}

        c = HamClockCollector()
        fc = c._fetch()

        assert fc["properties"]["space_weather"]["source"] == "NOAA SWPC"
        assert fc["properties"]["hamclock"]["available"] is False
        assert fc["properties"]["hamclock"]["source"] == "NOAA SWPC"
        # No band_conditions, voacap, de, dx, dxspots keys when HamClock is down
        assert "band_conditions" not in fc["properties"]["hamclock"]
        assert "voacap" not in fc["properties"]["hamclock"]
        assert "de_station" not in fc["properties"]["hamclock"]
        assert "dxspots" not in fc["properties"]["hamclock"]

    # --- Reliability to status ---

    def test_reliability_to_status(self):
        assert HamClockCollector._reliability_to_status(90) == "excellent"
        assert HamClockCollector._reliability_to_status(70) == "good"
        assert HamClockCollector._reliability_to_status(50) == "fair"
        assert HamClockCollector._reliability_to_status(20) == "poor"
        assert HamClockCollector._reliability_to_status(0) == "closed"


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


    @patch.object(MeshtasticCollector, "collect")
    @patch.object(ReticulumCollector, "collect")
    @patch.object(HamClockCollector, "collect")
    @patch.object(AREDNCollector, "collect")
    def test_collect_all_caches_overlay_data(self, mock_aredn, mock_ham, mock_ret, mock_mesh):
        mock_mesh.return_value = make_feature_collection([], "meshtastic")
        mock_ret.return_value = make_feature_collection([], "reticulum")
        mock_aredn.return_value = make_feature_collection([], "aredn")
        # HamClock returns overlay in properties
        ham_fc = make_feature_collection([], "hamclock")
        ham_fc["properties"]["space_weather"] = {"solar_flux": 150}
        ham_fc["properties"]["solar_terminator"] = {"subsolar_lat": 10}
        mock_ham.return_value = ham_fc

        agg = DataAggregator(dict(DEFAULT_CONFIG_SUBSET))
        result = agg.collect_all()
        assert result["properties"]["overlay_data"]["space_weather"]["solar_flux"] == 150
        # Cached overlay should now be populated
        cached = agg.get_cached_overlay()
        assert cached["space_weather"]["solar_flux"] == 150
        assert cached["solar_terminator"]["subsolar_lat"] == 10

    def test_get_cached_overlay_empty_initially(self):
        agg = DataAggregator({
            "enable_meshtastic": False, "enable_reticulum": False,
            "enable_hamclock": False, "enable_aredn": False,
        })
        assert agg.get_cached_overlay() == {}

    def test_shutdown_is_safe(self):
        agg = DataAggregator({
            "enable_meshtastic": False, "enable_reticulum": False,
            "enable_hamclock": False, "enable_aredn": False,
        })
        agg.shutdown()  # Should not raise
        assert agg._mqtt_subscriber is None

    def test_clear_all_caches_resets_overlay(self):
        agg = DataAggregator({
            "enable_meshtastic": False, "enable_reticulum": False,
            "enable_hamclock": False, "enable_aredn": False,
        })
        agg._cached_overlay = {"test": True}
        agg.clear_all_caches()
        assert agg._cached_overlay == {}

    def test_get_source_health_returns_per_collector(self):
        config = dict(DEFAULT_CONFIG_SUBSET)
        agg = DataAggregator(config)
        health = agg.get_source_health()
        assert "meshtastic" in health
        assert "reticulum" in health
        assert "hamclock" in health
        assert "aredn" in health
        for name, info in health.items():
            assert info["source"] == name
            assert info["total_collections"] == 0
            assert info["total_errors"] == 0

    def test_get_source_health_empty_when_no_sources(self):
        agg = DataAggregator({
            "enable_meshtastic": False, "enable_reticulum": False,
            "enable_hamclock": False, "enable_aredn": False,
        })
        assert agg.get_source_health() == {}

    @patch.object(MeshtasticCollector, "collect")
    @patch.object(ReticulumCollector, "collect")
    @patch.object(HamClockCollector, "collect")
    @patch.object(AREDNCollector, "collect")
    def test_collector_failure_doesnt_crash(self, mock_aredn, mock_ham, mock_ret, mock_mesh):
        mock_mesh.side_effect = RuntimeError("connection failed")
        mock_ret.return_value = make_feature_collection(
            [make_feature("r1", 5.0, 6.0, "reticulum")], "reticulum"
        )
        mock_ham.return_value = make_feature_collection([], "hamclock")
        mock_aredn.return_value = make_feature_collection([], "aredn")

        agg = DataAggregator(dict(DEFAULT_CONFIG_SUBSET))
        result = agg.collect_all()
        assert result["properties"]["sources"]["meshtastic"] == 0
        assert result["properties"]["sources"]["reticulum"] == 1
        assert result["properties"]["total_nodes"] == 1

    def test_last_collect_age_none_before_collect(self):
        agg = DataAggregator({
            "enable_meshtastic": False, "enable_reticulum": False,
            "enable_hamclock": False, "enable_aredn": False,
        })
        assert agg.last_collect_age_seconds is None

    @patch.object(MeshtasticCollector, "collect")
    @patch.object(ReticulumCollector, "collect")
    @patch.object(HamClockCollector, "collect")
    @patch.object(AREDNCollector, "collect")
    def test_last_collect_age_after_collect(self, mock_aredn, mock_ham, mock_ret, mock_mesh):
        mock_mesh.return_value = make_feature_collection([], "meshtastic")
        mock_ret.return_value = make_feature_collection([], "reticulum")
        mock_ham.return_value = make_feature_collection([], "hamclock")
        mock_aredn.return_value = make_feature_collection([], "aredn")

        agg = DataAggregator(dict(DEFAULT_CONFIG_SUBSET))
        agg.collect_all()
        age = agg.last_collect_age_seconds
        assert age is not None
        assert age >= 0
        assert age < 5  # Should be very recent

    @patch.object(MeshtasticCollector, "collect")
    @patch.object(ReticulumCollector, "collect")
    @patch.object(HamClockCollector, "collect")
    @patch.object(AREDNCollector, "collect")
    def test_last_collect_counts_populated(self, mock_aredn, mock_ham, mock_ret, mock_mesh):
        mock_mesh.return_value = make_feature_collection(
            [make_feature("m1", 1.0, 2.0, "meshtastic")], "meshtastic"
        )
        mock_ret.return_value = make_feature_collection([], "reticulum")
        mock_ham.return_value = make_feature_collection([], "hamclock")
        mock_aredn.return_value = make_feature_collection([], "aredn")

        agg = DataAggregator(dict(DEFAULT_CONFIG_SUBSET))
        agg.collect_all()
        counts = agg.last_collect_counts
        assert counts["meshtastic"] == 1
        assert counts["reticulum"] == 0
        # Verify it's a copy (not mutable reference)
        counts["meshtastic"] = 999
        assert agg.last_collect_counts["meshtastic"] == 1


# Helper config for aggregator tests
DEFAULT_CONFIG_SUBSET = {
    "enable_meshtastic": True,
    "enable_reticulum": True,
    "enable_hamclock": True,
    "enable_aredn": True,
    "cache_ttl_minutes": 15,
}
