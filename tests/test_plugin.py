"""Tests for MeshForgeMapsPlugin lifecycle and event handling."""

from unittest.mock import MagicMock, patch

import pytest

from src.main import MeshForgeMapsPlugin


class TestPluginActivation:
    """Tests for plugin activate/deactivate lifecycle."""

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_activate_checks_start_result(self, MockConfig, MockServer):
        mock_config = MockConfig.return_value
        mock_server = MockServer.return_value
        mock_server.start.return_value = True
        mock_server.port = 8808

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)

        mock_server.start.assert_called_once()
        context.notify.assert_called_once()
        assert "started" in context.notify.call_args[0][1]

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_activate_notifies_on_failure(self, MockConfig, MockServer):
        mock_server = MockServer.return_value
        mock_server.start.return_value = False

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)

        # Should notify about failure, not register tools
        assert "Failed" in context.notify.call_args[0][1]
        context.register_tool.assert_not_called()

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_deactivate_stops_server(self, MockConfig, MockServer):
        mock_server = MockServer.return_value
        mock_server.start.return_value = True
        mock_server.port = 8808

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)
        plugin.deactivate()

        mock_server.stop.assert_called_once()
        MockConfig.return_value.save.assert_called_once()

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_deactivate_clears_server_ref(self, MockConfig, MockServer):
        mock_server = MockServer.return_value
        mock_server.start.return_value = True
        mock_server.port = 8808

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)
        plugin.deactivate()
        assert plugin._server is None


class TestPluginEventHandlers:
    """Tests for event handler functionality."""

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_on_node_discovered_clears_cache(self, MockConfig, MockServer):
        mock_server = MockServer.return_value
        mock_server.start.return_value = True
        mock_server.port = 8808

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)
        plugin._on_node_discovered({"id": "test_node"})

        mock_server.aggregator.clear_all_caches.assert_called_once()

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_on_config_changed_updates_config(self, MockConfig, MockServer):
        mock_config = MockConfig.return_value
        mock_server = MockServer.return_value
        mock_server.start.return_value = True
        mock_server.port = 8808

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)
        plugin._on_config_changed({"http_port": 9999})

        mock_config.update.assert_called_with({"http_port": 9999})
        mock_config.save.assert_called()

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_on_config_changed_ignores_non_dict(self, MockConfig, MockServer):
        mock_config = MockConfig.return_value
        mock_server = MockServer.return_value
        mock_server.start.return_value = True
        mock_server.port = 8808

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)
        # Smoke test: verify no exception raised on non-dict input
        plugin._on_config_changed("invalid")

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_refresh_data(self, MockConfig, MockServer):
        mock_server = MockServer.return_value
        mock_server.start.return_value = True
        mock_server.port = 8808
        mock_server.aggregator.collect_all.return_value = {
            "properties": {"total_nodes": 42, "sources": {"meshtastic": 42}}
        }

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)
        result = plugin._refresh_data()

        assert "42" in result
        mock_server.aggregator.clear_all_caches.assert_called_once()

    def test_refresh_data_when_not_running(self):
        plugin = MeshForgeMapsPlugin()
        assert plugin._refresh_data() == "Server not running"

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_get_status(self, MockConfig, MockServer):
        mock_server = MockServer.return_value
        mock_server.start.return_value = True
        mock_server.port = 8808
        mock_server.aggregator.mqtt_subscriber = None
        mock_server.aggregator.enabled_collector_names = ["meshtastic", "reticulum"]

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)
        status = plugin._get_status()

        assert "8808" in status
        assert "meshtastic" in status

    def test_get_status_when_not_running(self):
        plugin = MeshForgeMapsPlugin()
        assert "not running" in plugin._get_status()

    # --- HamClock TUI tools ---

    def test_get_propagation_when_not_running(self):
        plugin = MeshForgeMapsPlugin()
        assert plugin._get_propagation() == "Server not running"

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_get_propagation_no_hamclock(self, MockConfig, MockServer):
        mock_server = MockServer.return_value
        mock_server.start.return_value = True
        mock_server.port = 8808
        mock_server.aggregator.get_collector.return_value = None

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)
        assert "not enabled" in plugin._get_propagation()

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_get_propagation_with_data(self, MockConfig, MockServer):
        mock_server = MockServer.return_value
        mock_server.start.return_value = True
        mock_server.port = 8808

        mock_hc = MagicMock()
        mock_hc.get_hamclock_data.return_value = {
            "source": "HamClock API",
            "available": True,
            "space_weather": {"solar_flux": "150", "kp_index": "2", "band_conditions": "good"},
            "voacap": {"bands": {"20m": {"reliability": 90, "status": "excellent"}}, "best_band": "20m"},
        }
        mock_server.aggregator.get_collector.return_value = mock_hc

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)
        result = plugin._get_propagation()
        assert "HamClock API" in result
        assert "SFI: 150" in result
        assert "20m" in result
        assert "90%" in result

    def test_get_dxspots_when_not_running(self):
        plugin = MeshForgeMapsPlugin()
        assert plugin._get_dxspots() == "Server not running"

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_get_dxspots_with_spots(self, MockConfig, MockServer):
        mock_server = MockServer.return_value
        mock_server.start.return_value = True
        mock_server.port = 8808

        mock_hc = MagicMock()
        mock_hc.get_hamclock_data.return_value = {
            "available": True,
            "dxspots": [
                {"dx_call": "JA1ABC", "freq_khz": "14250", "de_call": "W6XYZ", "utc": "1430"},
            ],
        }
        mock_server.aggregator.get_collector.return_value = mock_hc

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)
        result = plugin._get_dxspots()
        assert "JA1ABC" in result
        assert "14250" in result

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_get_dxspots_unavailable(self, MockConfig, MockServer):
        mock_server = MockServer.return_value
        mock_server.start.return_value = True
        mock_server.port = 8808

        mock_hc = MagicMock()
        mock_hc.get_hamclock_data.return_value = {"available": False}
        mock_server.aggregator.get_collector.return_value = mock_hc

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)
        assert "not available" in plugin._get_dxspots()

    def test_get_hamclock_status_when_not_running(self):
        plugin = MeshForgeMapsPlugin()
        assert plugin._get_hamclock_status() == "Server not running"

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_get_hamclock_status_connected(self, MockConfig, MockServer):
        mock_server = MockServer.return_value
        mock_server.start.return_value = True
        mock_server.port = 8808

        mock_hc = MagicMock()
        mock_hc.get_hamclock_data.return_value = {
            "available": True,
            "host": "192.168.1.50",
            "port": 8080,
            "source": "HamClock API",
            "de_station": {"call": "WH6GXZ", "grid": "BL11"},
            "dx_station": {"call": "F5ABC", "grid": "JN18"},
            "dxspots": [{"dx_call": "JA1ABC"}],
        }
        mock_server.aggregator.get_collector.return_value = mock_hc

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)
        result = plugin._get_hamclock_status()
        assert "CONNECTED" in result
        assert "192.168.1.50" in result
        assert "WH6GXZ" in result
        assert "F5ABC" in result
        assert "1 active" in result

    @patch("src.main.MapServer")
    @patch("src.main.MapsConfig")
    def test_activate_registers_hamclock_tools(self, MockConfig, MockServer):
        mock_server = MockServer.return_value
        mock_server.start.return_value = True
        mock_server.port = 8808

        context = MagicMock()
        context.settings = {}

        plugin = MeshForgeMapsPlugin()
        plugin.activate(context)

        # Should register 5 tools total (2 original + 3 new HamClock tools)
        assert context.register_tool.call_count == 5
        tool_ids = [call[1]["tool_id"] for call in context.register_tool.call_args_list]
        assert "meshforge_maps_propagation" in tool_ids
        assert "meshforge_maps_dxspots" in tool_ids
        assert "meshforge_maps_hamclock_status" in tool_ids
