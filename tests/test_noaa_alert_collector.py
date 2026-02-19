"""Tests for NOAA Weather Alert Collector."""

import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.collectors.noaa_alert_collector import (
    NOAAAlertCollector,
    SEVERITY_COLORS,
    SEVERITY_ORDER,
)
from src.collectors.base import make_feature_collection


def _make_noaa_feature(
    alert_id="urn:oid:2.49.0.1.840.0.test",
    event="Tornado Warning",
    severity="Extreme",
    certainty="Observed",
    urgency="Immediate",
    headline="Tornado Warning issued for Test County",
    area_desc="Test County, TX",
    onset=None,
    expires=None,
    geometry=None,
):
    """Build a minimal NOAA-style GeoJSON feature for testing."""
    if geometry is None:
        geometry = {
            "type": "Polygon",
            "coordinates": [[
                [-97.0, 32.0],
                [-97.0, 33.0],
                [-96.0, 33.0],
                [-96.0, 32.0],
                [-97.0, 32.0],
            ]],
        }
    if onset is None:
        onset = datetime.now(timezone.utc).isoformat()
    if expires is None:
        expires = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "id": alert_id,
            "event": event,
            "severity": severity,
            "certainty": certainty,
            "urgency": urgency,
            "headline": headline,
            "description": "A tornado warning has been issued.",
            "areaDesc": area_desc,
            "senderName": "NWS Fort Worth TX",
            "onset": onset,
            "expires": expires,
            "status": "Actual",
            "messageType": "Alert",
        },
    }


def _make_noaa_response(features=None):
    """Build a minimal NOAA API response."""
    if features is None:
        features = [_make_noaa_feature()]
    return {
        "type": "FeatureCollection",
        "features": features,
    }


class TestNOAAAlertCollector:
    """Tests for NOAAAlertCollector."""

    def test_source_name(self):
        c = NOAAAlertCollector()
        assert c.source_name == "noaa_alerts"

    def test_build_url_default(self):
        c = NOAAAlertCollector()
        url = c._build_url()
        assert "api.weather.gov/alerts/active" in url
        assert "status=actual" in url

    def test_build_url_with_area(self):
        c = NOAAAlertCollector(area="TX")
        url = c._build_url()
        assert "area=TX" in url

    def test_build_url_with_severity_filter(self):
        c = NOAAAlertCollector(severity_filter=["Extreme", "Severe"])
        url = c._build_url()
        assert "severity=Extreme,Severe" in url

    @patch("src.collectors.noaa_alert_collector.urlopen")
    def test_fetch_success(self, mock_urlopen):
        response_data = _make_noaa_response()
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        c = NOAAAlertCollector()
        result = c._fetch()

        assert result["type"] == "FeatureCollection"
        assert len(result["features"]) == 1
        assert result["properties"]["alert_count"] == 1
        assert result["features"][0]["properties"]["event"] == "Tornado Warning"

    @patch("src.collectors.noaa_alert_collector.urlopen")
    def test_fetch_network_error_returns_empty(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("Connection refused")

        c = NOAAAlertCollector()
        result = c._fetch()

        assert result["type"] == "FeatureCollection"
        assert len(result["features"]) == 0

    @patch("src.collectors.noaa_alert_collector.urlopen")
    def test_fetch_json_error_returns_empty(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        c = NOAAAlertCollector()
        result = c._fetch()

        assert result["type"] == "FeatureCollection"
        assert len(result["features"]) == 0


class TestProcessFeatures:
    """Tests for feature processing logic."""

    def test_filters_features_without_geometry(self):
        c = NOAAAlertCollector()
        feature_no_geom = _make_noaa_feature()
        feature_no_geom["geometry"] = None

        result = c._process_features([feature_no_geom])
        assert len(result) == 0

    def test_includes_polygon_features(self):
        c = NOAAAlertCollector()
        feature = _make_noaa_feature()
        result = c._process_features([feature])
        assert len(result) == 1
        assert result[0]["geometry"]["type"] == "Polygon"

    def test_deduplicates_by_alert_id(self):
        c = NOAAAlertCollector()
        f1 = _make_noaa_feature(alert_id="urn:test:1")
        f2 = _make_noaa_feature(alert_id="urn:test:1")
        result = c._process_features([f1, f2])
        assert len(result) == 1

    def test_keeps_unique_alerts(self):
        c = NOAAAlertCollector()
        f1 = _make_noaa_feature(alert_id="urn:test:1")
        f2 = _make_noaa_feature(alert_id="urn:test:2")
        result = c._process_features([f1, f2])
        assert len(result) == 2

    def test_filters_expired_alerts(self):
        c = NOAAAlertCollector()
        expired = _make_noaa_feature(
            expires=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        )
        result = c._process_features([expired])
        assert len(result) == 0

    def test_keeps_active_alerts(self):
        c = NOAAAlertCollector()
        active = _make_noaa_feature(
            expires=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        )
        result = c._process_features([active])
        assert len(result) == 1

    def test_enriches_with_severity_color(self):
        c = NOAAAlertCollector()
        feature = _make_noaa_feature(severity="Extreme")
        result = c._process_features([feature])
        assert result[0]["properties"]["color"] == SEVERITY_COLORS["Extreme"]

    def test_enriches_with_severity_order(self):
        c = NOAAAlertCollector()
        feature = _make_noaa_feature(severity="Severe")
        result = c._process_features([feature])
        assert result[0]["properties"]["severity_order"] == SEVERITY_ORDER["Severe"]

    def test_sorts_by_severity(self):
        c = NOAAAlertCollector()
        minor = _make_noaa_feature(alert_id="urn:minor", severity="Minor")
        extreme = _make_noaa_feature(alert_id="urn:extreme", severity="Extreme")
        moderate = _make_noaa_feature(alert_id="urn:moderate", severity="Moderate")

        result = c._process_features([minor, extreme, moderate])
        severities = [f["properties"]["severity"] for f in result]
        assert severities == ["Extreme", "Moderate", "Minor"]

    def test_maps_noaa_properties(self):
        c = NOAAAlertCollector()
        feature = _make_noaa_feature(
            event="Flash Flood Warning",
            area_desc="Dallas County, TX",
            certainty="Likely",
            urgency="Expected",
        )
        result = c._process_features([feature])
        props = result[0]["properties"]
        assert props["network"] == "noaa_alerts"
        assert props["event"] == "Flash Flood Warning"
        assert props["area_desc"] == "Dallas County, TX"
        assert props["certainty"] == "Likely"
        assert props["urgency"] == "Expected"
        assert props["sender_name"] == "NWS Fort Worth TX"

    def test_handles_multipolygon_geometry(self):
        c = NOAAAlertCollector()
        feature = _make_noaa_feature(geometry={
            "type": "MultiPolygon",
            "coordinates": [
                [[[-97.0, 32.0], [-97.0, 33.0], [-96.0, 33.0], [-96.0, 32.0], [-97.0, 32.0]]],
                [[[-95.0, 31.0], [-95.0, 32.0], [-94.0, 32.0], [-94.0, 31.0], [-95.0, 31.0]]],
            ],
        })
        result = c._process_features([feature])
        assert len(result) == 1
        assert result[0]["geometry"]["type"] == "MultiPolygon"

    def test_handles_unparseable_expires(self):
        """Alert with malformed expires date is kept (not filtered)."""
        c = NOAAAlertCollector()
        feature = _make_noaa_feature()
        feature["properties"]["expires"] = "not-a-date"
        result = c._process_features([feature])
        assert len(result) == 1

    def test_unknown_severity_gets_default_color(self):
        c = NOAAAlertCollector()
        feature = _make_noaa_feature(severity="Unknown")
        result = c._process_features([feature])
        assert result[0]["properties"]["color"] == SEVERITY_COLORS["Unknown"]


class TestSeverityConstants:
    """Tests for severity color and order constants."""

    def test_all_severity_levels_have_colors(self):
        for level in ["Extreme", "Severe", "Moderate", "Minor", "Unknown"]:
            assert level in SEVERITY_COLORS

    def test_all_severity_levels_have_order(self):
        for level in ["Extreme", "Severe", "Moderate", "Minor", "Unknown"]:
            assert level in SEVERITY_ORDER

    def test_order_is_ascending_severity(self):
        assert SEVERITY_ORDER["Extreme"] < SEVERITY_ORDER["Severe"]
        assert SEVERITY_ORDER["Severe"] < SEVERITY_ORDER["Moderate"]
        assert SEVERITY_ORDER["Moderate"] < SEVERITY_ORDER["Minor"]
        assert SEVERITY_ORDER["Minor"] < SEVERITY_ORDER["Unknown"]


class TestCacheIntegration:
    """Tests for caching behavior inherited from BaseCollector."""

    @patch("src.collectors.noaa_alert_collector.urlopen")
    def test_collect_uses_cache(self, mock_urlopen):
        response_data = _make_noaa_response()
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        c = NOAAAlertCollector(cache_ttl_seconds=300)
        result1 = c.collect()
        result2 = c.collect()

        # Second call should use cache — only one HTTP request
        assert mock_urlopen.call_count == 1
        assert result1 is result2

    @patch("src.collectors.noaa_alert_collector.urlopen")
    def test_health_info_tracks_collections(self, mock_urlopen):
        response_data = _make_noaa_response()
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        c = NOAAAlertCollector(cache_ttl_seconds=0)  # No caching
        c.collect()

        health = c.health_info
        assert health["source"] == "noaa_alerts"
        assert health["total_collections"] == 1
        assert health["total_errors"] == 0


class TestConfigIntegration:
    """Tests for config system integration."""

    def test_config_has_enable_noaa_alerts(self):
        from src.utils.config import DEFAULT_CONFIG
        assert "enable_noaa_alerts" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["enable_noaa_alerts"] is True

    def test_config_has_noaa_area_setting(self):
        from src.utils.config import DEFAULT_CONFIG
        assert "noaa_alerts_area" in DEFAULT_CONFIG

    def test_config_has_noaa_severity_setting(self):
        from src.utils.config import DEFAULT_CONFIG
        assert "noaa_alerts_severity" in DEFAULT_CONFIG

    def test_network_colors_include_noaa(self):
        from src.utils.config import NETWORK_COLORS
        assert "noaa_alerts" in NETWORK_COLORS

    def test_enabled_sources_includes_noaa(self):
        from src.utils.config import MapsConfig
        config = MapsConfig(config_path=None)
        sources = config.get_enabled_sources()
        assert "noaa_alerts" in sources

    def test_disabled_noaa_excluded_from_sources(self, tmp_path):
        from src.utils.config import MapsConfig
        config_path = tmp_path / "settings.json"
        config_path.write_text(json.dumps({"enable_noaa_alerts": False}))
        config = MapsConfig(config_path=config_path)
        sources = config.get_enabled_sources()
        assert "noaa_alerts" not in sources


class TestAggregatorIntegration:
    """Tests for aggregator integration."""

    def test_aggregator_registers_noaa_collector(self):
        from src.collectors.aggregator import DataAggregator
        agg = DataAggregator({
            "enable_meshtastic": False,
            "enable_reticulum": False,
            "enable_hamclock": False,
            "enable_aredn": False,
            "enable_noaa_alerts": True,
        })
        collector = agg.get_collector("noaa_alerts")
        assert collector is not None
        assert collector.source_name == "noaa_alerts"

    def test_aggregator_skips_disabled_noaa(self):
        from src.collectors.aggregator import DataAggregator
        agg = DataAggregator({
            "enable_meshtastic": False,
            "enable_reticulum": False,
            "enable_hamclock": False,
            "enable_aredn": False,
            "enable_noaa_alerts": False,
        })
        collector = agg.get_collector("noaa_alerts")
        assert collector is None

    def test_collect_all_excludes_noaa_features(self):
        """NOAA alerts (polygons) should not appear in collect_all (node points)."""
        from src.collectors.aggregator import DataAggregator
        agg = DataAggregator({
            "enable_meshtastic": False,
            "enable_reticulum": False,
            "enable_hamclock": False,
            "enable_aredn": False,
            "enable_noaa_alerts": True,
        })
        result = agg.collect_all()
        # collect_all should not include noaa_alerts features
        for f in result.get("features", []):
            assert f.get("properties", {}).get("network") != "noaa_alerts"

    def test_collect_source_returns_noaa_data(self):
        """collect_source('noaa_alerts') should return the NOAA collector output."""
        from src.collectors.aggregator import DataAggregator
        agg = DataAggregator({
            "enable_meshtastic": False,
            "enable_reticulum": False,
            "enable_hamclock": False,
            "enable_aredn": False,
            "enable_noaa_alerts": True,
        })
        result = agg.collect_source("noaa_alerts")
        assert result["type"] == "FeatureCollection"
        assert result["properties"]["source"] == "noaa_alerts"
