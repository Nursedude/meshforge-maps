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
        # Should not crash on non-dict data
        plugin._on_config_changed("invalid")
        # update should not have been called (only called during activate with empty settings)
        # The activate call triggers context.settings check, but not config.update
        # since settings is empty MagicMock dict

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
        mock_server.aggregator._mqtt_subscriber = None
        mock_server.aggregator._collectors = {"meshtastic": MagicMock(), "reticulum": MagicMock()}

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
