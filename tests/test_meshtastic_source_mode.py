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


class TestLockContentionRetry:
    """Tests for lock acquisition retry on meshtasticd API fetch."""

    @patch.object(MeshtasticCollector, "_fetch_from_mqtt_cache", return_value=[])
    @patch.object(MeshtasticCollector, "_fetch_from_live_mqtt", return_value=[])
    def test_api_fetch_retries_on_lock_contention(self, mock_live, mock_cache):
        """Lock contention on first attempt, success on second."""
        collector = MeshtasticCollector(source_mode="local_only")

        call_count = 0
        acquired_values = [False, True]  # First fails, second succeeds

        class FakeContext:
            def __init__(self, acquired_val):
                self._acquired = acquired_val

            def __enter__(self):
                return self._acquired

            def __exit__(self, *args):
                pass

        original_acquire = collector._conn_mgr.acquire

        def fake_acquire(**kwargs):
            nonlocal call_count
            idx = min(call_count, len(acquired_values) - 1)
            call_count += 1
            return FakeContext(acquired_values[idx])

        with patch.object(collector._conn_mgr, "acquire", side_effect=fake_acquire):
            with patch("time.sleep"):
                result = collector._fetch_from_api()

        # Should have retried (2 acquire calls)
        assert call_count == 2

    @patch.object(MeshtasticCollector, "_fetch_from_mqtt_cache", return_value=[])
    @patch.object(MeshtasticCollector, "_fetch_from_live_mqtt", return_value=[])
    def test_api_fetch_gives_up_after_two_lock_failures(self, mock_live, mock_cache):
        """Both lock attempts fail — returns empty list."""
        collector = MeshtasticCollector(source_mode="local_only")

        class FakeContext:
            def __enter__(self):
                return False

            def __exit__(self, *args):
                pass

        with patch.object(collector._conn_mgr, "acquire", return_value=FakeContext()):
            with patch("time.sleep"):
                result = collector._fetch_from_api()

        assert result == []
