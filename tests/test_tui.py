"""Tests for the MeshForge Maps TUI module.

Tests cover:
  - MapDataClient HTTP fetching
  - TuiApp initialization and state management
  - Color/attribute helpers
  - Drawing helpers (safe_addstr, draw_hbar)
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
        assert client.config_drift_summary() is None
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

    def test_init_defaults(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        assert app._active_tab == 0
        assert app._running is False
        assert app._connected is False

    def test_init_custom_port(self):
        from src.tui.app import TuiApp
        app = TuiApp(host="10.0.0.1", port=9999)
        assert app._client.base_url == "http://10.0.0.1:9999"

    def test_tab_names(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        assert len(app.TAB_NAMES) == 4
        assert "Dashboard" in app.TAB_NAMES
        assert "Nodes" in app.TAB_NAMES
        assert "Alerts" in app.TAB_NAMES
        assert "Propagation" in app.TAB_NAMES

    def test_scroll_initialized(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        for i in range(len(app.TAB_NAMES)):
            assert app._scroll[i] == 0


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
