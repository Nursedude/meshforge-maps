"""Tests for MeshtasticCollector source_mode (auto / mqtt_only / local_only)."""

from unittest.mock import MagicMock, patch

from src.collectors.meshtastic_collector import MeshtasticCollector


class TestSourceModeDefault:
    """Default source_mode is 'auto' -- all sources called."""

    def test_default_source_mode_is_auto(self):
        collector = MeshtasticCollector()
        assert collector._source_mode == "auto"

    @patch.object(MeshtasticCollector, "_fetch_from_mqtt_cache", return_value=[])
    @patch.object(MeshtasticCollector, "_fetch_from_live_mqtt", return_value=[])
    @patch.object(MeshtasticCollector, "_fetch_from_api", return_value=[])
    def test_auto_calls_all_sources(self, mock_api, mock_live, mock_cache):
        collector = MeshtasticCollector(source_mode="auto")
        collector._fetch()
        mock_api.assert_called_once()
        mock_live.assert_called_once()
        mock_cache.assert_called_once()


class TestSourceModeMQTTOnly:
    """mqtt_only mode skips the local meshtasticd API entirely."""

    @patch.object(MeshtasticCollector, "_fetch_from_mqtt_cache", return_value=[])
    @patch.object(MeshtasticCollector, "_fetch_from_live_mqtt", return_value=[])
    @patch.object(MeshtasticCollector, "_fetch_from_api", return_value=[])
    def test_mqtt_only_skips_api(self, mock_api, mock_live, mock_cache):
        collector = MeshtasticCollector(source_mode="mqtt_only")
        collector._fetch()
        mock_api.assert_not_called()
        mock_live.assert_called_once()
        mock_cache.assert_called_once()

    def test_mqtt_only_source_mode_stored(self):
        collector = MeshtasticCollector(source_mode="mqtt_only")
        assert collector._source_mode == "mqtt_only"


class TestSourceModeLocalOnly:
    """local_only mode skips MQTT sources, only uses local API."""

    @patch.object(MeshtasticCollector, "_fetch_from_mqtt_cache", return_value=[])
    @patch.object(MeshtasticCollector, "_fetch_from_live_mqtt", return_value=[])
    @patch.object(MeshtasticCollector, "_fetch_from_api", return_value=[])
    def test_local_only_skips_mqtt(self, mock_api, mock_live, mock_cache):
        collector = MeshtasticCollector(source_mode="local_only")
        collector._fetch()
        mock_api.assert_called_once()
        mock_live.assert_not_called()
        mock_cache.assert_not_called()
