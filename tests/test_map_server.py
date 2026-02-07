"""Tests for MapServer reliability improvements."""

import json
import threading
import time
from http.server import HTTPServer
from unittest.mock import MagicMock, patch

import pytest

from src.map_server import MapRequestHandler, MapServer
from src.utils.config import MapsConfig


class TestMapServerStartup:
    """Tests for MapServer start/stop lifecycle."""

    def test_start_returns_true_on_success(self, tmp_path):
        config = MapsConfig(config_path=tmp_path / "settings.json")
        config.set("http_port", 18808)
        server = MapServer(config)
        try:
            assert server.start() is True
            assert server.port == 18808
        finally:
            server.stop()

    def test_start_returns_false_on_all_ports_busy(self, tmp_path):
        config = MapsConfig(config_path=tmp_path / "settings.json")
        config.set("http_port", 18810)
        # Occupy 5 consecutive ports
        blockers = []
        try:
            for offset in range(5):
                s = HTTPServer(("127.0.0.1", 18810 + offset), MapRequestHandler)
                blockers.append(s)
            server = MapServer(config)
            assert server.start() is False
            assert server.port == 0
        finally:
            for s in blockers:
                s.server_close()

    def test_start_falls_back_to_next_port(self, tmp_path):
        config = MapsConfig(config_path=tmp_path / "settings.json")
        config.set("http_port", 18820)
        # Block the primary port
        blocker = HTTPServer(("127.0.0.1", 18820), MapRequestHandler)
        try:
            server = MapServer(config)
            assert server.start() is True
            assert server.port == 18821  # Should fall back to +1
        finally:
            server.stop()
            blocker.server_close()

    def test_stop_is_idempotent(self, tmp_path):
        config = MapsConfig(config_path=tmp_path / "settings.json")
        config.set("http_port", 18830)
        server = MapServer(config)
        server.start()
        server.stop()
        server.stop()  # Second stop should not raise

    def test_handler_uses_server_instance_state(self, tmp_path):
        config = MapsConfig(config_path=tmp_path / "settings.json")
        config.set("http_port", 18840)
        server = MapServer(config)
        try:
            assert server.start() is True
            # The server object should have instance-level attributes
            assert hasattr(server._server, "_mf_aggregator")
            assert hasattr(server._server, "_mf_config")
            assert hasattr(server._server, "_mf_web_dir")
        finally:
            server.stop()


class TestMapRequestHandlerAccessors:
    """Tests for handler instance-level state access."""

    def test_get_aggregator_missing(self):
        handler = MapRequestHandler.__new__(MapRequestHandler)
        handler.server = MagicMock(spec=[])  # no _mf_aggregator
        assert handler._get_aggregator() is None

    def test_get_aggregator_present(self):
        handler = MapRequestHandler.__new__(MapRequestHandler)
        mock_agg = MagicMock()
        handler.server = MagicMock()
        handler.server._mf_aggregator = mock_agg
        assert handler._get_aggregator() is mock_agg

    def test_get_config_missing(self):
        handler = MapRequestHandler.__new__(MapRequestHandler)
        handler.server = MagicMock(spec=[])
        assert handler._get_config() is None

    def test_get_web_dir_missing(self):
        handler = MapRequestHandler.__new__(MapRequestHandler)
        handler.server = MagicMock(spec=[])
        assert handler._get_web_dir() is None


class TestMapServerPort:
    """Tests for the port property."""

    def test_port_zero_before_start(self, tmp_path):
        config = MapsConfig(config_path=tmp_path / "settings.json")
        server = MapServer(config)
        assert server.port == 0
