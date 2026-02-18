"""Tests for MapServer reliability improvements."""

import json
import threading
import time
from http.server import HTTPServer
from unittest.mock import MagicMock, patch

import pytest

from src.map_server import MapRequestHandler, MapServer, MapServerContext, MeshForgeHTTPServer
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
            # The server object should have a typed context
            assert hasattr(server._server, "context")
            assert isinstance(server._server.context, MapServerContext)
            assert server._server.context.aggregator is not None
            assert server._server.context.config is not None
        finally:
            server.stop()


class TestMapRequestHandlerAccessors:
    """Tests for handler typed context access."""

    def _make_handler(self, ctx=None):
        handler = MapRequestHandler.__new__(MapRequestHandler)
        mock_server = MagicMock(spec=MeshForgeHTTPServer)
        mock_server.context = ctx or MapServerContext()
        handler.server = mock_server
        return handler

    def test_get_aggregator_missing(self):
        handler = self._make_handler()
        assert handler._get_aggregator() is None

    def test_get_aggregator_present(self):
        mock_agg = MagicMock()
        handler = self._make_handler(MapServerContext(aggregator=mock_agg))
        assert handler._get_aggregator() is mock_agg

    def test_get_config_missing(self):
        handler = self._make_handler()
        assert handler._get_config() is None

    def test_get_web_dir_missing(self):
        handler = self._make_handler()
        assert handler._get_web_dir() is None


class TestMapServerPort:
    """Tests for the port property."""

    def test_port_zero_before_start(self, tmp_path):
        config = MapsConfig(config_path=tmp_path / "settings.json")
        server = MapServer(config)
        assert server.port == 0


class TestMapServerHTTPEndpoints:
    """Integration tests for HTTP API endpoint responses."""

    @pytest.fixture(autouse=True)
    def _setup_server(self, tmp_path):
        """Start a real server for HTTP integration testing."""
        from urllib.request import urlopen, Request
        self.urlopen = urlopen
        self.Request = Request
        config = MapsConfig(config_path=tmp_path / "settings.json")
        config.set("http_port", 18850)
        # Disable all collectors to avoid real network calls
        config.set("enable_meshtastic", False)
        config.set("enable_reticulum", False)
        config.set("enable_hamclock", False)
        config.set("enable_aredn", False)
        self.server = MapServer(config)
        assert self.server.start() is True
        self.base = f"http://127.0.0.1:{self.server.port}"
        # Allow server thread to start accepting
        time.sleep(0.1)
        yield
        self.server.stop()

    def _get_json(self, path):
        req = self.Request(self.base + path, headers={"Accept": "application/json"})
        with self.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())

    def test_geojson_endpoint_returns_feature_collection(self):
        data = self._get_json("/api/nodes/geojson")
        assert data["type"] == "FeatureCollection"
        assert "features" in data
        assert isinstance(data["features"], list)
        assert "properties" in data
        assert "total_nodes" in data["properties"]

    def test_status_endpoint_returns_health(self):
        data = self._get_json("/api/status")
        assert data["status"] == "ok"
        assert data["extension"] == "meshforge-maps"
        assert "version" in data
        assert "uptime_seconds" in data
        assert isinstance(data["sources"], list)
        assert "mqtt_live" in data

    def test_topology_endpoint_returns_links(self):
        data = self._get_json("/api/topology")
        assert "links" in data
        assert isinstance(data["links"], list)
        assert "link_count" in data
        assert data["link_count"] == len(data["links"])

    def test_sources_endpoint_returns_sources(self):
        data = self._get_json("/api/sources")
        assert "sources" in data
        assert isinstance(data["sources"], list)
        assert "network_colors" in data

    def test_tile_providers_endpoint(self):
        data = self._get_json("/api/tile-providers")
        assert isinstance(data, dict)
        assert len(data) > 0
        # Each provider should have required fields
        for key, provider in data.items():
            assert "name" in provider
            assert "url" in provider

    def test_overlay_endpoint_returns_dict(self):
        data = self._get_json("/api/overlay")
        assert isinstance(data, dict)

    def test_config_endpoint_returns_settings(self):
        data = self._get_json("/api/config")
        assert isinstance(data, dict)
        assert "network_colors" in data

    def test_hamclock_endpoint_not_enabled(self):
        """HamClock endpoint returns 404 when source is disabled."""
        from urllib.error import HTTPError
        try:
            self._get_json("/api/hamclock")
            # If it returns without error, check the response
            assert False, "Expected 404"
        except HTTPError as e:
            assert e.code == 404

    def test_status_includes_source_health(self):
        data = self._get_json("/api/status")
        assert "source_health" in data
        assert isinstance(data["source_health"], dict)

    def test_health_endpoint_returns_score(self):
        data = self._get_json("/api/health")
        assert "score" in data
        assert isinstance(data["score"], int)
        assert 0 <= data["score"] <= 100
        assert "status" in data
        assert data["status"] in ("healthy", "fair", "degraded", "critical")
        assert "components" in data
        assert "freshness" in data["components"]
        assert "sources" in data["components"]
        assert "circuit_breakers" in data["components"]
        # Each component has score and max
        for component in data["components"].values():
            assert "score" in component
            assert "max" in component

    # --- Phase 3 endpoints ---

    def test_topology_geojson_endpoint(self):
        data = self._get_json("/api/topology/geojson")
        assert data["type"] == "FeatureCollection"
        assert "features" in data
        assert isinstance(data["features"], list)
        assert "properties" in data
        assert "link_count" in data["properties"]

    def test_trajectory_endpoint_empty(self):
        data = self._get_json("/api/nodes/!deadbeef/trajectory")
        assert data["type"] == "FeatureCollection"
        assert data["features"] == []

    def test_node_history_endpoint_empty(self):
        data = self._get_json("/api/nodes/!deadbeef/history")
        assert data["node_id"] == "!deadbeef"
        assert data["observations"] == []
        assert data["count"] == 0

    def test_snapshot_endpoint(self):
        data = self._get_json("/api/snapshot/1700000000")
        assert data["type"] == "FeatureCollection"
        assert isinstance(data["features"], list)

    def test_tracked_nodes_endpoint(self):
        data = self._get_json("/api/history/nodes")
        assert "nodes" in data
        assert isinstance(data["nodes"], list)
        assert "total_nodes" in data
        assert "total_observations" in data

    def test_core_health_endpoint(self):
        data = self._get_json("/api/core-health")
        assert "available" in data
        # Core won't be running in tests, so available should be False
        assert isinstance(data["available"], bool)

    def test_trajectory_with_query_params(self):
        data = self._get_json("/api/nodes/!a1b2c3d4/trajectory?since=1000&until=2000")
        assert data["type"] == "FeatureCollection"

    def test_node_history_with_limit(self):
        data = self._get_json("/api/nodes/!a1b2c3d4/history?limit=10")
        assert "observations" in data

    # --- Session 10 endpoints ---

    def test_config_drift_endpoint(self):
        data = self._get_json("/api/config-drift")
        assert "drifts" in data
        assert "total" in data

    def test_config_drift_summary_endpoint(self):
        data = self._get_json("/api/config-drift/summary")
        assert "tracked_nodes" in data
        assert "nodes_with_drift" in data

    def test_node_states_endpoint(self):
        data = self._get_json("/api/node-states")
        assert "states" in data
        assert "summary" in data

    def test_node_states_summary_endpoint(self):
        data = self._get_json("/api/node-states/summary")
        assert "tracked_nodes" in data
        assert "states" in data
        assert "total_transitions" in data

    def test_proxy_stats_endpoint(self):
        data = self._get_json("/api/proxy/stats")
        # Proxy not started because meshtastic is disabled
        assert "available" in data or "running" in data

    # --- Session 20: Export endpoints ---

    def _get_raw(self, path):
        """Fetch raw bytes from endpoint."""
        from urllib.request import urlopen, Request
        req = Request(self.base + path)
        with urlopen(req, timeout=5) as resp:
            return resp.read(), resp.headers

    def test_export_nodes_csv(self):
        body, headers = self._get_raw("/api/export/nodes")
        assert b"node_id" in body  # CSV header
        ct = headers.get("Content-Type", "")
        assert "text/csv" in ct
        disp = headers.get("Content-Disposition", "")
        assert "meshforge_nodes.csv" in disp

    def test_export_nodes_json(self):
        data = self._get_json("/api/export/nodes?format=json")
        assert "nodes" in data
        assert isinstance(data["nodes"], list)

    def test_export_alerts_csv(self):
        body, headers = self._get_raw("/api/export/alerts")
        assert b"timestamp" in body  # CSV header
        ct = headers.get("Content-Type", "")
        assert "text/csv" in ct

    def test_export_alerts_json(self):
        data = self._get_json("/api/export/alerts?format=json")
        assert "alerts" in data
        assert isinstance(data["alerts"], list)

    def test_export_analytics_growth_csv(self):
        body, headers = self._get_raw("/api/export/analytics/growth")
        assert b"timestamp" in body
        assert "text/csv" in headers.get("Content-Type", "")

    def test_export_analytics_activity_csv(self):
        body, headers = self._get_raw("/api/export/analytics/activity")
        assert b"hour" in body
        assert "text/csv" in headers.get("Content-Type", "")

    def test_export_analytics_ranking_csv(self):
        body, headers = self._get_raw("/api/export/analytics/ranking")
        assert b"rank" in body
        assert "text/csv" in headers.get("Content-Type", "")
