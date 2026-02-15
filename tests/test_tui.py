"""Tests for the MeshForge Maps TUI module.

Tests cover:
  - MapDataClient HTTP fetching
  - TuiApp initialization and state management
  - Color/attribute helpers
  - Drawing helpers (safe_addstr)
  - CLI argument parsing
"""

import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── Data Client Tests ──────────────────────────────────────────────

class TestMapDataClient:
    """Tests for the HTTP data client."""

    def test_init_default(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient()
        assert client.base_url == "http://127.0.0.1:8808"

    def test_init_custom(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(host="10.0.0.1", port=9999)
        assert client.base_url == "http://10.0.0.1:9999"

    def test_get_returns_none_on_connection_error(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=1)  # port 1 should not have a server
        result = client._get("/api/health")
        assert result is None

    def test_is_alive_returns_false_when_no_server(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=1)
        assert client.is_alive() is False

    def test_all_accessors_return_none_on_failure(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=1)
        assert client.server_status() is None
        assert client.health_check() is None
        assert client.nodes_geojson() is None
        assert client.node_health_summary() is None
        assert client.all_node_health() is None
        assert client.node_states_summary() is None
        assert client.all_node_states() is None
        assert client.alerts() is None
        assert client.active_alerts() is None
        assert client.alert_summary() is None
        assert client.alert_rules() is None
        assert client.topology() is None
        assert client.sources() is None
        assert client.hamclock() is None
        assert client.perf_stats() is None
        assert client.analytics_summary() is None
        assert client.circuit_breaker_states() is None
        assert client.mqtt_stats() is None


class _StubHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that returns JSON for test endpoints."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "path": self.path}).encode())

    def log_message(self, format, *args):
        pass  # suppress logs


class TestMapDataClientWithServer:
    """Tests that verify actual HTTP communication."""

    @pytest.fixture(autouse=True)
    def _start_stub_server(self):
        self.server = HTTPServer(("127.0.0.1", 0), _StubHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        yield
        self.server.shutdown()

    def test_get_returns_json(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=self.port)
        result = client._get("/api/health")
        assert result is not None
        assert result["ok"] is True
        assert result["path"] == "/api/health"

    def test_is_alive_true(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=self.port)
        assert client.is_alive() is True

    def test_server_status_returns_dict(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=self.port)
        result = client.server_status()
        assert isinstance(result, dict)

    def test_nodes_geojson_returns_dict(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=self.port)
        result = client.nodes_geojson()
        assert isinstance(result, dict)


# ── TUI App Tests ──────────────────────────────────────────────────

class TestTuiAppInit:
    """Tests for TuiApp initialization."""

    def test_init_custom_port(self):
        from src.tui.app import TuiApp
        app = TuiApp(host="10.0.0.1", port=9999)
        assert app._client.base_url == "http://10.0.0.1:9999"



# ── Color/Attribute Helpers ────────────────────────────────────────

class TestColorHelpers:
    """Tests for color mapping functions (without initializing curses)."""

    def test_health_color_returns_int(self):
        """health_color should return a curses color pair integer."""
        # We can't fully test curses color pairs without a terminal,
        # but we can verify the function doesn't crash with valid labels.
        from src.tui.app import health_color
        with patch("src.tui.app.curses") as mock_curses:
            mock_curses.color_pair.return_value = 42
            result = health_color("excellent")
            assert result == 42

    def test_severity_color_returns_int(self):
        from src.tui.app import severity_color
        with patch("src.tui.app.curses") as mock_curses:
            mock_curses.color_pair.return_value = 99
            result = severity_color("critical")
            assert result == 99

    def test_health_color_unknown_label(self):
        from src.tui.app import health_color
        with patch("src.tui.app.curses") as mock_curses:
            mock_curses.color_pair.return_value = 0
            result = health_color("nonexistent")
            # Falls through to CP_NORMAL = 0
            mock_curses.color_pair.assert_called_with(0)

    def test_severity_color_unknown_severity(self):
        from src.tui.app import severity_color
        with patch("src.tui.app.curses") as mock_curses:
            mock_curses.color_pair.return_value = 0
            result = severity_color("unknown")
            mock_curses.color_pair.assert_called_with(0)


# ── Drawing Helpers ────────────────────────────────────────────────

class TestSafeAddstr:
    """Tests for the safe_addstr helper."""

    def test_clips_to_window_width(self):
        from src.tui.app import safe_addstr
        win = MagicMock()
        win.getmaxyx.return_value = (24, 10)
        safe_addstr(win, 0, 0, "Hello World!", 0)
        # Should clip to 9 chars (cols=10, x=0, margin=1)
        win.addstr.assert_called_once_with(0, 0, "Hello Wor", 0)

    def test_skips_if_y_out_of_bounds(self):
        from src.tui.app import safe_addstr
        win = MagicMock()
        win.getmaxyx.return_value = (24, 80)
        safe_addstr(win, 30, 0, "test", 0)
        win.addstr.assert_not_called()

    def test_skips_if_x_out_of_bounds(self):
        from src.tui.app import safe_addstr
        win = MagicMock()
        win.getmaxyx.return_value = (24, 80)
        safe_addstr(win, 0, 100, "test", 0)
        win.addstr.assert_not_called()

    def test_respects_max_width(self):
        from src.tui.app import safe_addstr
        win = MagicMock()
        win.getmaxyx.return_value = (24, 80)
        safe_addstr(win, 0, 0, "Hello World!", 0, max_width=5)
        win.addstr.assert_called_once_with(0, 0, "Hello", 0)

    def test_handles_curses_error(self):
        """Should not raise on curses.error (e.g. writing to bottom-right corner)."""
        import curses as _curses
        from src.tui.app import safe_addstr
        win = MagicMock()
        win.getmaxyx.return_value = (24, 80)
        win.addstr.side_effect = _curses.error("test")
        # Should not raise
        safe_addstr(win, 0, 0, "test", 0)


# ── Timestamp Formatter ───────────────────────────────────────────

class TestFormatTs:
    def test_zero_returns_placeholder(self):
        from src.tui.app import _format_ts
        assert _format_ts(0) == "--:--:--"

    def test_valid_timestamp(self):
        from src.tui.app import _format_ts
        result = _format_ts(1700000000)
        # Should return HH:MM:SS format
        assert len(result) == 8
        assert result[2] == ":" and result[5] == ":"

    def test_none_returns_placeholder(self):
        from src.tui.app import _format_ts
        assert _format_ts(None) == "--:--:--"


# ── CLI Argument Parsing ──────────────────────────────────────────

class TestArgParsing:
    """Tests for the --tui / --tui-only argument parsing."""

    def test_parse_default_args(self):
        from src.main import _parse_args
        with patch("sys.argv", ["meshforge-maps"]):
            args = _parse_args()
            assert args.tui is False
            assert args.tui_only is False
            assert args.host == "127.0.0.1"
            assert args.port == 0

    def test_parse_tui_flag(self):
        from src.main import _parse_args
        with patch("sys.argv", ["meshforge-maps", "--tui"]):
            args = _parse_args()
            assert args.tui is True
            assert args.tui_only is False

    def test_parse_tui_only_flag(self):
        from src.main import _parse_args
        with patch("sys.argv", ["meshforge-maps", "--tui-only"]):
            args = _parse_args()
            assert args.tui_only is True

    def test_parse_custom_host_port(self):
        from src.main import _parse_args
        with patch("sys.argv", ["meshforge-maps", "--host", "10.0.0.5", "--port", "9000"]):
            args = _parse_args()
            assert args.host == "10.0.0.5"
            assert args.port == 9000

    def test_parse_tui_with_port(self):
        from src.main import _parse_args
        with patch("sys.argv", ["meshforge-maps", "--tui", "--port", "8810"]):
            args = _parse_args()
            assert args.tui is True
            assert args.port == 8810


# ── Input Handling ─────────────────────────────────────────────────

class TestInputHandling:
    """Test keyboard input handling logic."""

    def _make_app(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        app._running = True
        app._stdscr = MagicMock()
        return app

    def test_quit_on_q(self):
        app = self._make_app()
        app._stdscr.getch.return_value = ord("q")
        app._handle_input()
        assert app._running is False

    def test_quit_on_Q(self):
        app = self._make_app()
        app._stdscr.getch.return_value = ord("Q")
        app._handle_input()
        assert app._running is False

    def test_tab_switch_by_number(self):
        app = self._make_app()
        with patch.object(app, "_refresh_data"):
            app._stdscr.getch.return_value = ord("2")
            app._handle_input()
            assert app._active_tab == 1

            app._stdscr.getch.return_value = ord("4")
            app._handle_input()
            assert app._active_tab == 3

    def test_scroll_down(self):
        import curses as _curses
        app = self._make_app()
        app._stdscr.getch.return_value = ord("j")
        app._handle_input()
        assert app._scroll[0] == 1

    def test_scroll_up_clamped(self):
        app = self._make_app()
        app._stdscr.getch.return_value = ord("k")
        app._handle_input()
        assert app._scroll[0] == 0  # Can't go below 0

    def test_scroll_up(self):
        app = self._make_app()
        app._scroll[0] = 5
        app._stdscr.getch.return_value = ord("k")
        app._handle_input()
        assert app._scroll[0] == 4

    def test_home_resets_scroll(self):
        app = self._make_app()
        app._scroll[0] = 50
        app._stdscr.getch.return_value = ord("g")
        app._handle_input()
        assert app._scroll[0] == 0

    def test_no_key_does_nothing(self):
        app = self._make_app()
        app._stdscr.getch.return_value = -1
        old_tab = app._active_tab
        app._handle_input()
        assert app._active_tab == old_tab
        assert app._running is True

    def test_sort_toggle_on_nodes_tab(self):
        app = self._make_app()
        app._active_tab = 1  # Nodes tab
        app._stdscr.getch.return_value = ord("s")
        app._handle_input()
        assert app._node_sort == (0, True)  # Toggled reverse

    def test_sort_column_cycle_on_nodes_tab(self):
        app = self._make_app()
        app._active_tab = 1  # Nodes tab
        app._stdscr.getch.return_value = ord("S")
        app._handle_input()
        assert app._node_sort == (1, False)  # Next column


# ── Refresh Logic ──────────────────────────────────────────────────

class TestRefreshData:
    """Test background data refresh logic."""

    def test_refresh_sets_connected_false_on_failure(self):
        from src.tui.app import TuiApp
        app = TuiApp(port=1)  # No server
        app._refresh_data()
        assert app._connected is False
        assert app._error_msg != ""

    def test_refresh_populates_cache_on_success(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        # Mock the client to return data
        app._client = MagicMock()
        app._client.is_alive.return_value = True
        app._client.base_url = "http://127.0.0.1:8808"
        app._client.server_status.return_value = {"port": 8808}
        app._client.health_check.return_value = {"status": "ok"}
        app._client.sources.return_value = {"meshtastic": {"enabled": True}}
        app._client.perf_stats.return_value = {}
        app._client.mqtt_stats.return_value = {}
        # Dashboard-specific
        app._client.node_health_summary.return_value = {"total_nodes": 5}
        app._client.node_states_summary.return_value = {}
        app._client.alert_summary.return_value = {}
        app._client.analytics_summary.return_value = {}
        app._client.circuit_breaker_states.return_value = {}

        app._active_tab = 0
        app._refresh_data()

        assert app._connected is True
        assert app._cache.get("status") == {"port": 8808}
        assert app._cache.get("node_health_summary") == {"total_nodes": 5}

    def test_refresh_fetches_topology_data_on_topology_tab(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        app._client = MagicMock()
        app._client.is_alive.return_value = True
        app._client.base_url = "http://127.0.0.1:8808"
        app._client.server_status.return_value = {"port": 8808}
        app._client.health_check.return_value = {"status": "ok"}
        app._client.sources.return_value = {}
        app._client.perf_stats.return_value = {}
        app._client.mqtt_stats.return_value = {}
        app._client.topology_geojson.return_value = {"features": []}
        app._client.nodes_geojson.return_value = {"features": []}

        app._active_tab = 4  # Topology tab
        app._refresh_data()

        assert app._connected is True
        app._client.topology_geojson.assert_called_once()

    def test_refresh_fetches_node_detail_when_drill_down_active(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        app._client = MagicMock()
        app._client.is_alive.return_value = True
        app._client.base_url = "http://127.0.0.1:8808"
        app._client.server_status.return_value = {"port": 8808}
        app._client.health_check.return_value = {"status": "ok"}
        app._client.sources.return_value = {}
        app._client.perf_stats.return_value = {}
        app._client.mqtt_stats.return_value = {}
        app._client.nodes_geojson.return_value = {"features": []}
        app._client.all_node_health.return_value = {}
        app._client.all_node_states.return_value = {}
        app._client.node_health.return_value = {"score": 85}
        app._client.node_history.return_value = {"observations": []}
        app._client.node_alerts.return_value = {"alerts": []}
        app._client.config_drift.return_value = {"recent_drifts": []}

        app._active_tab = 1  # Nodes tab
        app._detail_node_id = "!abc123"
        app._refresh_data()

        app._client.node_health.assert_called_once_with("!abc123")
        app._client.node_history.assert_called_once_with("!abc123")
        app._client.node_alerts.assert_called_once_with("!abc123")


# ── New Data Client Accessor Tests ─────────────────────────────────

class TestNewDataClientAccessors:
    """Tests for the new per-node and topology client methods."""

    def test_node_health_returns_none_on_failure(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=1)
        assert client.node_health("!abc") is None

    def test_node_history_returns_none_on_failure(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=1)
        assert client.node_history("!abc") is None

    def test_node_alerts_returns_none_on_failure(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=1)
        assert client.node_alerts("!abc") is None

    def test_topology_geojson_returns_none_on_failure(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=1)
        assert client.topology_geojson() is None

    def test_config_drift_returns_none_on_failure(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=1)
        assert client.config_drift() is None


class TestNewDataClientWithServer:
    """Tests for new accessors against the stub HTTP server."""

    @pytest.fixture(autouse=True)
    def _start_stub_server(self):
        self.server = HTTPServer(("127.0.0.1", 0), _StubHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        yield
        self.server.shutdown()

    def test_node_health_fetches_correct_path(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=self.port)
        result = client.node_health("!abc123")
        assert result is not None
        assert "/api/nodes/!abc123/health" in result["path"]

    def test_node_history_fetches_correct_path(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=self.port)
        result = client.node_history("!abc123", limit=10)
        assert result is not None
        assert "/api/nodes/!abc123/history" in result["path"]
        assert "limit=10" in result["path"]

    def test_node_alerts_fetches_correct_path(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=self.port)
        result = client.node_alerts("!abc123")
        assert result is not None
        assert "node_id=!abc123" in result["path"]

    def test_topology_geojson_fetches_correct_path(self):
        from src.tui.data_client import MapDataClient
        client = MapDataClient(port=self.port)
        result = client.topology_geojson()
        assert result is not None
        assert "/api/topology/geojson" in result["path"]


# ── Node Detail View Tests ────────────────────────────────────────

class TestNodeDetailDrillDown:
    """Tests for node detail drill-down feature."""

    def _make_app(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        app._running = True
        app._stdscr = MagicMock()
        return app

    def test_escape_exits_detail_view(self):
        app = self._make_app()
        app._active_tab = 1
        app._detail_node_id = "!abc123"
        app._stdscr.getch.return_value = 27  # Escape
        app._handle_input()
        assert app._detail_node_id is None

    def test_q_exits_detail_view_not_app(self):
        app = self._make_app()
        app._active_tab = 1
        app._detail_node_id = "!abc123"
        app._stdscr.getch.return_value = ord("q")
        app._handle_input()
        assert app._detail_node_id is None
        assert app._running is True  # App still running

    def test_q_exits_app_when_not_in_detail(self):
        app = self._make_app()
        app._detail_node_id = None
        app._stdscr.getch.return_value = ord("q")
        app._handle_input()
        assert app._running is False

    def test_tab_switch_clears_detail(self):
        app = self._make_app()
        app._detail_node_id = "!abc123"
        with patch.object(app, "_refresh_data"):
            app._stdscr.getch.return_value = ord("3")
            app._handle_input()
            assert app._detail_node_id is None
            assert app._active_tab == 2

    def test_enter_triggers_detail_on_nodes_tab(self):
        app = self._make_app()
        app._active_tab = 1
        app._cache = {
            "nodes": {
                "features": [
                    {"properties": {"id": "!abc", "name": "TestNode",
                                    "source": "meshtastic"}}
                ]
            },
            "all_node_health": {},
            "all_node_states": {},
        }
        app._node_cursor = 0
        with patch.object(app, "_refresh_data"):
            app._stdscr.getch.return_value = ord("\n")
            app._handle_input()
            assert app._detail_node_id == "!abc"

    def test_detail_scroll_j_k(self):
        app = self._make_app()
        app._active_tab = 1
        app._detail_node_id = "!abc"
        app._detail_scroll = 5
        app._stdscr.getch.return_value = ord("j")
        app._handle_input()
        assert app._detail_scroll == 6

        app._stdscr.getch.return_value = ord("k")
        app._handle_input()
        assert app._detail_scroll == 5

    def test_build_node_rows(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        cache = {
            "nodes": {
                "features": [
                    {"properties": {"id": "!abc", "name": "Alpha",
                                    "source": "meshtastic"}},
                    {"properties": {"id": "!def", "name": "Bravo",
                                    "source": "aredn"}},
                ]
            },
            "all_node_health": {
                "!abc": {"score": 85, "label": "good"},
            },
            "all_node_states": {
                "nodes": {
                    "!abc": {"state": "stable"},
                }
            },
        }
        rows = app._build_node_rows(cache)
        assert len(rows) == 2
        assert rows[0]["full_id"] in ("!abc", "!def")

    def test_node_cursor_clamping(self):
        app = self._make_app()
        app._active_tab = 1
        app._node_cursor = 100
        # Scroll down when already at large cursor
        app._stdscr.getch.return_value = ord("j")
        app._handle_input()
        assert app._node_cursor == 101  # Goes up, will be clamped during draw


# ── Topology Tab Tests ────────────────────────────────────────────

class TestTopologyTab:
    """Tests for topology ASCII art tab."""

    def test_tab_switch_to_topology(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        app._running = True
        app._stdscr = MagicMock()
        with patch.object(app, "_refresh_data"):
            app._stdscr.getch.return_value = ord("5")
            app._handle_input()
            assert app._active_tab == 4

    def test_quality_color_helper(self):
        from src.tui.app import _quality_color
        with patch("src.tui.app.curses") as mock_curses:
            mock_curses.color_pair.return_value = 42
            result = _quality_color("excellent")
            assert result == 42
            result = _quality_color("unknown")
            assert result == mock_curses.color_pair.return_value


# ── Events Tab Tests ──────────────────────────────────────────────

class TestEventsTab:
    """Tests for the event stream tab."""

    def test_tab_switch_to_events(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        app._running = True
        app._stdscr = MagicMock()
        with patch.object(app, "_refresh_data"):
            app._stdscr.getch.return_value = ord("6")
            app._handle_input()
            assert app._active_tab == 5

    def test_on_ws_message_appends_to_log(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        msg = {"type": "node.position", "timestamp": 1700000000,
               "node_id": "!abc", "data": {"lat": 40.0}}
        app._on_ws_message(msg)
        assert len(app._event_log) == 1
        assert app._event_log[0]["type"] == "node.position"

    def test_event_log_ring_buffer_truncation(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        app._event_log_max = 10
        for i in range(20):
            app._on_ws_message({"type": "test", "seq": i})
        assert len(app._event_log) == 10
        # Should keep the last 10
        assert app._event_log[0]["seq"] == 10
        assert app._event_log[-1]["seq"] == 19

    def test_event_type_color_helper(self):
        from src.tui.app import _event_type_color
        with patch("src.tui.app.curses") as mock_curses:
            mock_curses.color_pair.return_value = 5
            result = _event_type_color("alert.fired")
            assert result == 5
            result = _event_type_color("node.position")
            assert result == 5


# ── WebSocket State Tests ─────────────────────────────────────────

class TestWebSocketState:
    """Tests for WebSocket connection state management."""

    def test_ws_read_frame_returns_none_on_close(self):
        """Test that _ws_read_frame handles close frame correctly."""
        from src.tui.app import TuiApp
        app = TuiApp()
        # Mock socket that returns a close frame (opcode 0x8)
        mock_sock = MagicMock()
        # Close frame: FIN=1, opcode=8, length=0
        mock_sock.recv.return_value = bytes([0x88, 0x00])
        result = app._ws_read_frame(mock_sock)
        assert result is None

    def test_ws_read_frame_returns_text(self):
        """Test that _ws_read_frame can parse a simple text frame."""
        from src.tui.app import TuiApp
        import struct
        app = TuiApp()
        mock_sock = MagicMock()
        payload = b'{"type":"test"}'
        # Text frame: FIN=1 opcode=1, no mask, length=payload length
        frame = bytes([0x81, len(payload)]) + payload
        # recv returns bytes in sequence
        call_count = [0]
        def fake_recv(n):
            nonlocal call_count
            start = call_count[0]
            call_count[0] += n
            return frame[start:start + n]
        mock_sock.recv.side_effect = fake_recv
        result = app._ws_read_frame(mock_sock)
        assert result == '{"type":"test"}'

    def test_ws_read_frame_handles_error(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = OSError("connection reset")
        result = app._ws_read_frame(mock_sock)
        assert result is None


# ── Draw Method Tests (with mocked curses) ─────────────────────────

class TestDrawMethods:
    """Tests that draw methods don't crash with mock data."""

    def _make_app_with_screen(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        app._running = True
        app._connected = True
        win = MagicMock()
        win.getmaxyx.return_value = (40, 120)
        app._stdscr = win
        return app

    def test_draw_node_detail_with_data(self):
        app = self._make_app_with_screen()
        app._detail_node_id = "!abc123"
        cache = {
            "nodes": {
                "features": [
                    {"properties": {"id": "!abc123", "name": "TestNode",
                                    "source": "meshtastic"}}
                ]
            },
            "all_node_health": {"!abc123": {"score": 85, "label": "good"}},
            "all_node_states": {"nodes": {"!abc123": {"state": "stable"}}},
            "detail_health": {
                "score": 85, "status": "good",
                "components": {
                    "battery": {"score": 22.5, "max": 25, "battery_level": 75},
                    "signal": {"score": 20.0, "max": 25, "snr": 8.5},
                }
            },
            "detail_history": {
                "observations": [
                    {"timestamp": 1700000000, "latitude": 40.0,
                     "longitude": -105.0, "snr": 8.5, "battery": 75,
                     "network": "meshtastic"},
                ]
            },
            "detail_alerts": {"alerts": []},
            "config_drift": {"recent_drifts": [
                {"node_id": "!abc123", "field": "role",
                 "old_value": "CLIENT", "new_value": "ROUTER",
                 "severity": "warning", "timestamp": 1700000000},
            ]},
        }
        with patch("src.tui.app.curses") as mc:
            mc.color_pair.return_value = 0
            mc.A_BOLD = 1
            mc.A_UNDERLINE = 2
            mc.A_DIM = 4
            # Should not raise
            app._draw_node_detail(1, 38, 120, cache)

    def test_draw_node_detail_empty_data(self):
        app = self._make_app_with_screen()
        app._detail_node_id = "!missing"
        cache = {
            "nodes": {"features": []},
            "all_node_health": {},
            "all_node_states": {},
            "detail_health": None,
            "detail_history": None,
            "detail_alerts": None,
            "config_drift": None,
        }
        with patch("src.tui.app.curses") as mc:
            mc.color_pair.return_value = 0
            mc.A_BOLD = 1
            mc.A_UNDERLINE = 2
            mc.A_DIM = 4
            app._draw_node_detail(1, 38, 120, cache)

    def test_draw_topology_with_links(self):
        app = self._make_app_with_screen()
        cache = {
            "topo": {
                "features": [
                    {"properties": {"source": "!a", "target": "!b",
                                    "snr": 10.0, "quality": "excellent"}},
                    {"properties": {"source": "!b", "target": "!c",
                                    "snr": 3.0, "quality": "marginal"}},
                ]
            },
            "nodes": {
                "features": [
                    {"properties": {"id": "!a", "name": "Alpha"}},
                    {"properties": {"id": "!b", "name": "Bravo"}},
                    {"properties": {"id": "!c", "name": "Charlie"}},
                ]
            },
        }
        with patch("src.tui.app.curses") as mc:
            mc.color_pair.return_value = 0
            mc.A_BOLD = 1
            mc.A_UNDERLINE = 2
            mc.A_DIM = 4
            app._draw_topology(1, 38, 120, cache)

    def test_draw_topology_empty(self):
        app = self._make_app_with_screen()
        cache = {"topo": None, "nodes": {"features": []}}
        with patch("src.tui.app.curses") as mc:
            mc.color_pair.return_value = 0
            mc.A_BOLD = 1
            mc.A_UNDERLINE = 2
            mc.A_DIM = 4
            app._draw_topology(1, 38, 120, cache)

    def test_draw_events_empty(self):
        app = self._make_app_with_screen()
        with patch("src.tui.app.curses") as mc:
            mc.color_pair.return_value = 0
            mc.A_BOLD = 1
            mc.A_UNDERLINE = 2
            mc.A_DIM = 4
            app._draw_events(1, 38, 120)

    def test_draw_events_with_data(self):
        app = self._make_app_with_screen()
        app._event_log = [
            {"type": "node.position", "timestamp": 1700000000,
             "source": "mqtt", "node_id": "!abc",
             "data": {"lat": 40.0, "snr": 8.5}},
            {"type": "alert.fired", "timestamp": 1700000001,
             "source": "engine", "node_id": "!def",
             "data": {"severity": "critical", "message": "Low battery"}},
        ]
        with patch("src.tui.app.curses") as mc:
            mc.color_pair.return_value = 0
            mc.A_BOLD = 1
            mc.A_UNDERLINE = 2
            mc.A_DIM = 4
            app._draw_events(1, 38, 120)


class TestSearchFilter:
    """Tests for TUI search/filter functionality."""

    def _make_app(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        app._running = True
        app._connected = True
        win = MagicMock()
        win.getmaxyx.return_value = (40, 120)
        app._stdscr = win
        return app

    def test_search_activation(self):
        """Pressing / activates search mode."""
        app = self._make_app()
        app._stdscr.getch.return_value = ord("/")
        app._handle_input()
        assert app._search_active is True
        assert app._search_query == ""

    def test_search_typing(self):
        """Characters accumulate in search query during search mode."""
        app = self._make_app()
        app._search_active = True
        # Type 'a'
        app._stdscr.getch.return_value = ord("a")
        app._handle_input()
        assert app._search_query == "a"
        # Type 'b'
        app._stdscr.getch.return_value = ord("b")
        app._handle_input()
        assert app._search_query == "ab"

    def test_search_backspace(self):
        """Backspace removes last character in search."""
        app = self._make_app()
        app._search_active = True
        app._search_query = "test"
        app._stdscr.getch.return_value = 127  # Backspace
        app._handle_input()
        assert app._search_query == "tes"

    def test_search_escape_cancels(self):
        """Escape cancels search and clears query."""
        app = self._make_app()
        app._search_active = True
        app._search_query = "test"
        app._stdscr.getch.return_value = 27  # Escape
        app._handle_input()
        assert app._search_active is False
        assert app._search_query == ""

    def test_search_enter_accepts(self):
        """Enter accepts search and exits input mode."""
        app = self._make_app()
        app._search_active = True
        app._search_query = "meshtastic"
        app._stdscr.getch.return_value = ord("\n")
        app._handle_input()
        assert app._search_active is False
        assert app._search_query == "meshtastic"  # preserved

    def test_search_escape_clears_filter(self):
        """Escape clears existing filter when not in search input mode."""
        app = self._make_app()
        app._search_active = False
        app._search_query = "meshtastic"
        app._stdscr.getch.return_value = 27  # Escape
        app._handle_input()
        assert app._search_query == ""

class TestEventsPauseResume:
    """Tests for Events tab pause/resume and type filtering."""

    def _make_app(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        app._running = True
        app._connected = True
        win = MagicMock()
        win.getmaxyx.return_value = (40, 120)
        app._stdscr = win
        return app

    def test_pause_toggle(self):
        """Pressing p on Events tab toggles pause."""
        app = self._make_app()
        app._active_tab = 5
        app._event_log = [
            {"type": "node.position", "timestamp": 100, "node_id": "!a"},
        ]
        app._stdscr.getch.return_value = ord("p")
        app._handle_input()
        assert app._events_paused is True
        assert len(app._events_paused_snapshot) == 1

    def test_unpause(self):
        """Pressing p again unpauses."""
        app = self._make_app()
        app._active_tab = 5
        app._events_paused = True
        app._stdscr.getch.return_value = ord("p")
        app._handle_input()
        assert app._events_paused is False

    def test_event_type_filter_cycle(self):
        """Pressing f cycles through event type filters."""
        app = self._make_app()
        app._active_tab = 5
        assert app._event_type_filter is None
        app._stdscr.getch.return_value = ord("f")
        app._handle_input()
        assert app._event_type_filter == "node.position"
        app._stdscr.getch.return_value = ord("f")
        app._handle_input()
        assert app._event_type_filter == "node.telemetry"

    def test_event_type_filter_wraps(self):
        """Filter cycles back to None (all) after last type."""
        app = self._make_app()
        app._active_tab = 5
        # Cycle through all options
        num_options = len(app._event_type_options)
        for _ in range(num_options):
            app._stdscr.getch.return_value = ord("f")
            app._handle_input()
        assert app._event_type_filter is None  # Back to start

    def test_draw_events_paused(self):
        """Events tab renders correctly when paused."""
        app = self._make_app()
        app._events_paused = True
        app._events_paused_snapshot = [
            {"type": "node.position", "timestamp": 1700000000,
             "source": "mqtt", "node_id": "!abc",
             "data": {"lat": 40.0}},
        ]
        with patch("src.tui.app.curses") as mc:
            mc.color_pair.return_value = 0
            mc.A_BOLD = 1
            mc.A_UNDERLINE = 2
            mc.A_DIM = 4
            app._draw_events(1, 38, 120)

    def test_draw_events_with_type_filter(self):
        """Events tab renders correctly with type filter active."""
        app = self._make_app()
        app._event_type_filter = "alert.fired"
        app._event_log = [
            {"type": "node.position", "timestamp": 1700000000,
             "source": "mqtt", "node_id": "!abc", "data": {}},
            {"type": "alert.fired", "timestamp": 1700000001,
             "source": "engine", "node_id": "!def",
             "data": {"severity": "critical"}},
        ]
        with patch("src.tui.app.curses") as mc:
            mc.color_pair.return_value = 0
            mc.A_BOLD = 1
            mc.A_UNDERLINE = 2
            mc.A_DIM = 4
            app._draw_events(1, 38, 120)

    def test_p_key_only_on_events_tab(self):
        """Pressing p on non-Events tab does not toggle pause."""
        app = self._make_app()
        app._active_tab = 0  # Dashboard
        app._stdscr.getch.return_value = ord("p")
        app._handle_input()  # Should not error
        assert app._events_paused is False

    def test_f_key_only_on_events_tab(self):
        """Pressing f on non-Events tab does not change filter."""
        app = self._make_app()
        app._active_tab = 1  # Nodes
        app._stdscr.getch.return_value = ord("f")
        app._handle_input()
        assert app._event_type_filter is None
