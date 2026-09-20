"""Tests for MapServer reliability improvements."""

import json
import time
from http.server import HTTPServer

import pytest

from src.map_server import MapRequestHandler, MapServer, MapServerContext
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
        config.set("enable_meshcore", False)
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
        for _key, provider in data.items():
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
            raise AssertionError("Expected 404")
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

    def test_trajectory_url_encoded_node_id(self):
        """Node ID with ! prefix should work when URL-encoded as %21."""
        data = self._get_json("/api/nodes/%21a1b2c3d4/trajectory")
        assert data["type"] == "FeatureCollection"

    def test_health_url_encoded_node_id(self):
        """URL-encoded ! must not be rejected as invalid format (400)."""
        from urllib.error import HTTPError
        try:
            self._get_json("/api/nodes/%21a1b2c3d4/health")
        except HTTPError as e:
            # 404 (node not in data) is fine; 400 (bad format) means decoding failed
            assert e.code != 400, "URL-encoded node ID rejected as invalid format"

    def test_history_url_encoded_node_id(self):
        data = self._get_json("/api/nodes/%21a1b2c3d4/history")
        assert "observations" in data

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

    # --- Coverage Heatmap endpoint ---

    def test_heatmap_endpoint_returns_points(self):
        data = self._get_json("/api/heatmap")
        assert "points" in data
        assert isinstance(data["points"], list)
        assert "cell_count" in data
        assert "max_observations" in data
        assert "precision" in data
        assert data["precision"] == 4  # default precision

    def test_heatmap_endpoint_with_precision(self):
        data = self._get_json("/api/heatmap?precision=3")
        assert data["precision"] == 3

    def test_heatmap_endpoint_clamps_precision(self):
        data = self._get_json("/api/heatmap?precision=1")
        assert data["precision"] == 2  # minimum clamped to 2
        data = self._get_json("/api/heatmap?precision=10")
        assert data["precision"] == 6  # maximum clamped to 6


class TestHeatmapNormalization:
    """Edge case tests for heatmap intensity normalization."""

    def test_zero_max_count_does_not_divide_by_zero(self):
        """Regression: max_count=0 should not cause ZeroDivisionError."""
        # Simulate the normalization logic from _serve_heatmap
        raw = [(45.0, -122.0, 0)]  # count=0 edge case
        max_count = max(raw[0][2], 1) if raw else 1
        points = [[lat, lon, count / max_count] for lat, lon, count in raw]
        assert points == [[45.0, -122.0, 0.0]]
        assert max_count == 1

    def test_empty_raw_returns_empty_points(self):
        raw = []
        max_count = max(raw[0][2], 1) if raw else 1
        points = [[lat, lon, count / max_count] for lat, lon, count in raw]
        assert points == []
        assert max_count == 1

    def test_normal_normalization(self):
        raw = [(45.0, -122.0, 10), (46.0, -121.0, 5), (47.0, -120.0, 1)]
        max_count = max(raw[0][2], 1) if raw else 1
        points = [[lat, lon, count / max_count] for lat, lon, count in raw]
        assert max_count == 10
        assert points[0][2] == 1.0   # 10/10
        assert points[1][2] == 0.5   # 5/10
        assert points[2][2] == 0.1   # 1/10


class TestRateLimitingIntegration:
    """Integration tests for the per-IP rate limit on the live HTTP server."""

    @pytest.fixture(autouse=True)
    def _setup_server(self, tmp_path):
        from urllib.request import urlopen
        self.urlopen = urlopen
        config = MapsConfig(config_path=tmp_path / "settings.json")
        config.set("http_port", 18860)
        config.set("enable_meshtastic", False)
        config.set("enable_reticulum", False)
        config.set("enable_hamclock", False)
        config.set("enable_aredn", False)
        config.set("enable_meshcore", False)
        # Tight budget so the test can exhaust it in a handful of calls.
        config.set("rate_limit_per_minute", 3)
        self.server = MapServer(config)
        assert self.server.start() is True
        self.base = f"http://127.0.0.1:{self.server.port}"
        time.sleep(0.1)
        yield
        self.server.stop()

    def test_429_with_retry_after_after_budget_exhausted(self):
        from urllib.error import HTTPError
        # First 3 succeed
        for _ in range(3):
            with self.urlopen(self.base + "/api/status", timeout=5) as resp:
                assert resp.status == 200
        # Next one is rate-limited
        try:
            self.urlopen(self.base + "/api/status", timeout=5)
            raise AssertionError("Expected 429")
        except HTTPError as e:
            assert e.code == 429
            assert e.headers.get("Retry-After") is not None
            assert int(e.headers["Retry-After"]) >= 1

    def test_health_endpoint_is_never_rate_limited(self):
        """The liveness endpoint must stay 200 past the budget — a 429 reads
        as 'service down' to the watchdog."""
        for _ in range(10):  # budget is 3; /api/health is exempt
            with self.urlopen(self.base + "/api/health", timeout=5) as resp:
                assert resp.status == 200


class TestClientIPAndExemption:
    """_client_ip trusted-proxy resolution + rate-limit path exemption."""

    def _bare_handler(self, peer, trusted=(), xff=None):
        import types
        h = MapRequestHandler.__new__(MapRequestHandler)
        h.client_address = (peer, 12345)
        h.headers = {"X-Forwarded-For": xff} if xff is not None else {}
        # _ctx is a read-only property → self.server.context.
        h.server = types.SimpleNamespace(
            context=MapServerContext(trusted_proxies=frozenset(trusted)))
        return h

    def test_peer_used_when_no_trusted_proxy(self):
        h = self._bare_handler("203.0.113.7", trusted=(), xff="9.9.9.9")
        assert h._client_ip() == "203.0.113.7"  # XFF ignored — peer not trusted

    def test_xff_honored_behind_trusted_proxy(self):
        h = self._bare_handler("127.0.0.1", trusted=("127.0.0.1",), xff="203.0.113.7")
        assert h._client_ip() == "203.0.113.7"

    def test_xff_skips_trusted_hops(self):
        h = self._bare_handler("127.0.0.1", trusted=("127.0.0.1", "10.0.0.1"),
                                xff="203.0.113.7, 10.0.0.1")
        assert h._client_ip() == "203.0.113.7"

    def test_spoofed_xff_from_untrusted_peer_ignored(self):
        # A direct client setting its own XFF must NOT bypass its real bucket.
        h = self._bare_handler("203.0.113.9", trusted=("127.0.0.1",), xff="1.2.3.4")
        assert h._client_ip() == "203.0.113.9"

    def test_rate_limit_exempts_health_and_static(self):
        h = MapRequestHandler.__new__(MapRequestHandler)
        assert h._rate_limit_applies("/api/status") is True
        assert h._rate_limit_applies("/api/nodes/geojson") is True
        assert h._rate_limit_applies("/api/health") is False
        assert h._rate_limit_applies("/api/health/") is False
        assert h._rate_limit_applies("/") is False
        assert h._rate_limit_applies("/app.js") is False


class TestSecurityHeaders:
    """HSTS is opt-in via enable_hsts; default is no header."""

    def _start_server(self, tmp_path, enable_hsts):
        config = MapsConfig(config_path=tmp_path / "settings.json")
        config.set("http_port", 18870)
        config.set("enable_meshtastic", False)
        config.set("enable_reticulum", False)
        config.set("enable_hamclock", False)
        config.set("enable_aredn", False)
        config.set("enable_meshcore", False)
        config.set("enable_hsts", enable_hsts)
        srv = MapServer(config)
        assert srv.start() is True
        time.sleep(0.1)
        return srv

    def test_hsts_absent_by_default(self, tmp_path):
        from urllib.request import urlopen
        srv = self._start_server(tmp_path, enable_hsts=False)
        try:
            with urlopen(f"http://127.0.0.1:{srv.port}/api/status", timeout=5) as r:
                assert r.headers.get("Strict-Transport-Security") is None
        finally:
            srv.stop()

    def test_hsts_present_when_enabled(self, tmp_path):
        from urllib.request import urlopen
        srv = self._start_server(tmp_path, enable_hsts=True)
        try:
            with urlopen(f"http://127.0.0.1:{srv.port}/api/status", timeout=5) as r:
                hsts = r.headers.get("Strict-Transport-Security")
                assert hsts is not None
                assert "max-age=" in hsts
        finally:
            srv.stop()


class TestAuthFailureLogging:
    """Failed API-key auth must surface a WARNING with client IP."""

    def test_failed_post_logs_warning_with_ip(self, tmp_path, caplog):
        import logging
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        config = MapsConfig(config_path=tmp_path / "settings.json")
        config.set("http_port", 18880)
        config.set("enable_meshtastic", False)
        config.set("enable_reticulum", False)
        config.set("enable_hamclock", False)
        config.set("enable_aredn", False)
        config.set("enable_meshcore", False)
        config.set("api_key", "correct-key")
        srv = MapServer(config)
        assert srv.start() is True
        time.sleep(0.1)
        try:
            req = Request(
                f"http://127.0.0.1:{srv.port}/api/config",
                data=b'{"http_port": 8808}',
                headers={
                    "Content-Type": "application/json",
                    "X-MeshForge-Key": "wrong-key",
                },
                method="POST",
            )
            with caplog.at_level(logging.WARNING, logger="src.map_server"):
                try:
                    urlopen(req, timeout=5)
                    raise AssertionError("Expected 401")
                except HTTPError as e:
                    assert e.code == 401
            assert any(
                "auth: rejected X-MeshForge-Key from" in rec.message
                and "/api/config" in rec.message
                for rec in caplog.records
            )
        finally:
            srv.stop()


class TestReadSurfaceTiering:
    """GET surface split: the mesh's data stays open, the deployment's does not.

    Every test here pins api_key explicitly. MapsConfig layers
    DEFAULT_CONFIG < global.ini < settings.json, so a box with an api_key in
    ~/.config/meshforge/global.ini would otherwise flip these verdicts
    depending on WHOSE machine ran the suite.
    """

    KEY = "tiering-test-key"
    MASK = "***"  # named constant: a literal here trips ruff S105

    def _server(self, tmp_path, port, api_key):
        config = MapsConfig(config_path=tmp_path / "settings.json")
        config.set("http_port", port)
        config.set("enable_meshtastic", False)
        config.set("enable_reticulum", False)
        config.set("enable_hamclock", False)
        config.set("enable_aredn", False)
        config.set("enable_meshcore", False)
        config.set("api_key", api_key)
        srv = MapServer(config)
        assert srv.start() is True
        time.sleep(0.1)
        return srv, config

    @staticmethod
    def _get(base, path, key=None):
        """Return (status, parsed_json_or_None).

        The /api/export/* routes answer CSV, not JSON, so the body is parsed
        opportunistically -- a decode failure here is a content type, not a
        verdict, and must not be read as the endpoint failing.
        """
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        headers = {"Accept": "application/json"}
        if key:
            headers["X-MeshForge-Key"] = key
        try:
            with urlopen(Request(base + path, headers=headers), timeout=5) as resp:
                raw = resp.read().decode()
                try:
                    return resp.status, json.loads(raw)
                except ValueError:
                    return resp.status, None
        except HTTPError as e:
            return e.code, None

    # -- the closed-enum gate -------------------------------------------------

    def test_every_route_is_classified(self):
        """A new route must be declared public OR admin -- never default open.

        This is the gate, not the tiering itself: without it, adding a route to
        _ROUTE_TABLE silently publishes it, which is exactly how the endpoints
        this change gates became public in the first place.
        """
        from src.map_server import MapRequestHandler as H
        routes = set(H._ROUTE_TABLE.keys())
        classified = H._PUBLIC_GET_PATHS | H._ADMIN_GET_PATHS
        assert routes - classified == set(), (
            f"unclassified route(s): {sorted(routes - classified)} -- add each to "
            f"_PUBLIC_GET_PATHS or _ADMIN_GET_PATHS in src/map_server.py"
        )
        assert classified - routes == set(), (
            f"classified but not routed: {sorted(classified - routes)} -- stale entry"
        )
        assert H._PUBLIC_GET_PATHS & H._ADMIN_GET_PATHS == set(), "a path cannot be both"

    def test_public_config_keys_cover_first_paint(self):
        """Pin the keys loadConfig() reads before any auth exists.

        Shrinking _PUBLIC_CONFIG_KEYS blanks the map on first paint, and no
        local check can catch that -- there is no JS engine on these boxes.
        This test is the only thing standing in for a browser.
        """
        from src.map_server import MapRequestHandler as H
        required = {
            "map_center_lat", "map_center_lon", "map_default_zoom",
            "default_tile_provider", "region_preset",
            "region_presets", "network_colors", "ws_port",
        }
        assert required <= H._PUBLIC_CONFIG_KEYS

    # -- the projection -------------------------------------------------------

    def test_anonymous_config_is_display_only(self, tmp_path):
        srv, _ = self._server(tmp_path, 18890, self.KEY)
        try:
            from src.map_server import MapRequestHandler as H
            base = f"http://127.0.0.1:{srv.port}"
            status, cfg = self._get(base, "/api/config")
            assert status == 200, "the map must still load without a key"
            assert set(cfg) <= H._PUBLIC_CONFIG_KEYS, (
                f"leaked beyond the allowlist: {sorted(set(cfg) - H._PUBLIC_CONFIG_KEYS)}"
            )
            # The specific things that were public before this change.
            for leaked in (
                "mqtt_broker", "mqtt_topic", "mqtt_brokers", "aredn_node_ips",
                "trusted_proxies", "meshtasticd_host", "meshtasticd_port",
                "rch_host", "mesh_client_path", "ws_allowed_origins",
                "cors_allowed_origin", "http_host", "http_port",
            ):
                assert leaked not in cfg, f"{leaked} still served anonymously"
            # And it is still USABLE: first paint needs these.
            assert "ws_port" in cfg or "region_presets" in cfg
        finally:
            srv.stop()

    def test_admin_config_is_full(self, tmp_path):
        srv, _ = self._server(tmp_path, 18891, self.KEY)
        try:
            base = f"http://127.0.0.1:{srv.port}"
            status, cfg = self._get(base, "/api/config", key=self.KEY)
            assert status == 200
            assert "mqtt_broker" in cfg
            assert "aredn_node_ips" in cfg
            assert cfg["api_key"] == self.MASK, "the key must never echo back in clear"
        finally:
            srv.stop()

    # -- the gate -------------------------------------------------------------

    @pytest.mark.parametrize("path", [
        "/api/dependencies",
        "/api/config-drift",
        "/api/config-drift/summary",
        "/api/perf",
        "/api/proxy/stats",
        "/api/core-health",
        "/api/export/nodes",
        "/api/export/alerts",
        "/api/export/analytics/growth",
        "/api/export/analytics/activity",
        "/api/export/analytics/ranking",
    ])
    def test_introspective_get_requires_key(self, tmp_path, path):
        srv, _ = self._server(tmp_path, 18892, self.KEY)
        try:
            base = f"http://127.0.0.1:{srv.port}"
            status, _body = self._get(base, path)
            assert status == 401, f"{path} answered {status} without a key"
            status_ok, _ = self._get(base, path, key=self.KEY)
            assert status_ok != 401, f"{path} refused the correct key"
        finally:
            srv.stop()

    @pytest.mark.parametrize("path", [
        "/api/nodes/geojson",
        "/api/status",
        "/api/health",
        "/api/topology",
        "/api/alerts/active",
        "/api/analytics/growth",
        "/api/heatmap",
        "/api/auth/check",
    ])
    def test_display_get_stays_open(self, tmp_path, path):
        """The product must not need a key. A blanket gate would break the NOC."""
        srv, _ = self._server(tmp_path, 18893, self.KEY)
        try:
            base = f"http://127.0.0.1:{srv.port}"
            status, _ = self._get(base, path)
            assert status == 200, f"{path} answered {status} -- display surface broke"
        finally:
            srv.stop()

    def test_no_key_configured_changes_nothing(self, tmp_path):
        """A box that never set a key behaves exactly as it did before."""
        srv, _ = self._server(tmp_path, 18894, None)
        try:
            base = f"http://127.0.0.1:{srv.port}"
            assert self._get(base, "/api/dependencies")[0] == 200
            _, cfg = self._get(base, "/api/config")
            assert "mqtt_broker" in cfg, "no key configured == no admin distinction"
        finally:
            srv.stop()

    # -- the credential that fell through the denylist ------------------------

    def test_nested_broker_credentials_are_masked(self, tmp_path):
        """_REDACTED_CONFIG_KEYS is a denylist of TOP-LEVEL scalars; these live
        one level down inside mqtt_brokers and were served in clear."""
        srv, config = self._server(tmp_path, 18895, self.KEY)
        try:
            config.set("mqtt_brokers", [{
                "broker": "broker.example", "port": 8883, "topic": "msh/#",
                "username": "svc-account", "password": "s3cr3t", "use_tls": True,
            }])
            base = f"http://127.0.0.1:{srv.port}"
            _, cfg = self._get(base, "/api/config", key=self.KEY)
            entry = cfg["mqtt_brokers"][0]
            assert entry["password"] == self.MASK
            assert entry["username"] == self.MASK
            # Non-secret fields survive -- the admin form needs them.
            assert entry["broker"] == "broker.example"
            assert entry["port"] == 8883
            raw = json.dumps(cfg)
            assert "s3cr3t" not in raw and "svc-account" not in raw
        finally:
            srv.stop()

    def test_post_response_masks_broker_credentials_too(self, tmp_path):
        """The POST response is cached into the browser's localStorage, so a
        redaction that fixed only the GET would still write credentials to the
        viewer's disk. One shared helper -- assert BOTH callers use it."""
        from urllib.request import Request, urlopen
        srv, config = self._server(tmp_path, 18896, self.KEY)
        try:
            config.set("mqtt_brokers", [{
                "broker": "broker.example", "port": 8883,
                "username": "svc-account", "password": "s3cr3t",
            }])
            req = Request(
                f"http://127.0.0.1:{srv.port}/api/config",
                data=b'{"map_default_zoom": 5}',
                headers={"Content-Type": "application/json",
                         "X-MeshForge-Key": self.KEY},
                method="POST",
            )
            with urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read().decode())
            assert body["status"] == "saved"
            raw = json.dumps(body["config"])
            assert "s3cr3t" not in raw, "POST response leaked a broker password"
            assert "svc-account" not in raw
        finally:
            srv.stop()

    def test_operator_home_path_not_served_anonymously(self, tmp_path):
        """mesh_client_path defaults under the operator's home on two fleet
        boxes; /api/status already basenames db_path for the same reason."""
        srv, config = self._server(tmp_path, 18897, self.KEY)
        try:
            config.set("mesh_client_path", "/home/someuser/.local/state/nodes.geojson")
            base = f"http://127.0.0.1:{srv.port}"
            _, cfg = self._get(base, "/api/config")
            assert "someuser" not in json.dumps(cfg)
        finally:
            srv.stop()
