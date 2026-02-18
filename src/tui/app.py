"""MeshForge Maps TUI - Core Application

Curses-based terminal UI with tabbed panels for monitoring mesh network state.
Uses only Python stdlib (curses). Connects to a running MapServer via HTTP API.

Tabs:
  [1] Dashboard  - Server status, source health, node counts, perf stats
  [2] Nodes      - Scrollable node table with health scores and state
  [3] Alerts     - Live alert feed with severity coloring
  [4] Propagation - HF band conditions, space weather, DX spots
  [5] Topology   - ASCII mesh topology visualization
  [6] Events     - Live WebSocket event stream
"""

import base64
import curses
import json
import logging
import os
import socket
import struct
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .data_client import MapDataClient
from .helpers import (
    CP_ALERT_WARNING,
    CP_HEADER,
    CP_STATUS_BAR,
    CP_TAB_ACTIVE,
    _init_colors,
    safe_addstr,
)
from .tabs.dashboard import draw_dashboard
from .tabs.nodes import build_node_rows, draw_node_detail, draw_nodes
from .tabs.alerts import draw_alerts
from .tabs.propagation import draw_propagation
from .tabs.topology import draw_topology
from .tabs.events import draw_events

# Re-export helpers for backward compatibility (used by tests and external code)
from .helpers import (  # noqa: F401
    CP_NORMAL, CP_HEALTH_EXCELLENT, CP_HEALTH_GOOD, CP_HEALTH_FAIR,
    CP_HEALTH_POOR, CP_HEALTH_CRITICAL,
    CP_ALERT_INFO, CP_ALERT_CRITICAL,
    CP_SOURCE_ONLINE, CP_SOURCE_OFFLINE,
    CP_HIGHLIGHT, CP_DIM, CP_TAB_INACTIVE,
    health_color, severity_color, _format_ts, _quality_color, _event_type_color,
)

logger = logging.getLogger(__name__)

# Refresh interval for background data fetch (seconds)
REFRESH_INTERVAL = 5


class TuiApp:
    """Main TUI application controller."""

    TAB_NAMES = ["Dashboard", "Nodes", "Alerts", "Propagation", "Topology", "Events"]

    def __init__(self, host: str = "127.0.0.1", port: int = 8808):
        self._client = MapDataClient(host, port)
        self._active_tab = 0
        self._running = False
        self._stdscr: Any = None

        # Shared data cache (written by background thread, read by draw)
        self._data_lock = threading.Lock()
        self._cache: Dict[str, Any] = {}
        self._last_refresh = 0.0
        self._connected = False
        self._error_msg = ""

        # Per-tab scroll offsets
        self._scroll: Dict[int, int] = {i: 0 for i in range(len(self.TAB_NAMES))}

        # Node table sort: (column_index, reverse)
        self._node_sort: Tuple[int, bool] = (0, False)

        # Node detail drill-down state
        self._detail_node_id: Optional[str] = None
        self._detail_scroll: int = 0
        self._node_cursor: int = 0  # cursor position in node list

        # Event log ring buffer (for Events tab)
        self._event_log: List[Dict[str, Any]] = []
        self._event_log_max = 500

        # WebSocket state
        self._ws_connected = False
        self._ws_thread: Optional[threading.Thread] = None

        # Search/filter state
        self._search_active = False
        self._search_query = ""

        # Events tab: pause/resume and type filter
        self._events_paused = False
        self._events_paused_snapshot: List[Dict[str, Any]] = []
        self._event_type_filter: Optional[str] = None
        self._event_type_options = [
            None, "node.position", "node.telemetry",
            "node.topology", "alert.fired", "service",
        ]
        self._event_type_filter_idx = 0

    def run(self) -> None:
        """Launch the TUI (blocks until quit)."""
        curses.wrapper(self._main)

    def _main(self, stdscr: Any) -> None:
        self._stdscr = stdscr
        _init_colors()

        curses.curs_set(0)  # hide cursor
        stdscr.nodelay(False)
        stdscr.timeout(500)  # 500ms input timeout for responsive refresh

        self._running = True

        # Start background data fetcher
        fetch_thread = threading.Thread(target=self._fetch_loop, daemon=True)
        fetch_thread.start()

        # Start WebSocket listener for push updates
        self._ws_thread = threading.Thread(target=self._ws_listen_loop, daemon=True)
        self._ws_thread.start()

        # Initial data fetch
        self._refresh_data()

        while self._running:
            self._draw()
            self._handle_input()

    def _fetch_loop(self) -> None:
        """Background thread that periodically fetches fresh data."""
        while self._running:
            time.sleep(REFRESH_INTERVAL)
            if self._running:
                self._refresh_data()

    def _refresh_data(self) -> None:
        """Fetch all data from the MapServer API."""
        try:
            alive = self._client.is_alive()
            if not alive:
                with self._data_lock:
                    self._connected = False
                    self._error_msg = f"Cannot connect to {self._client.base_url}"
                return

            # Snapshot tab state under lock to avoid race with UI thread
            with self._data_lock:
                tab = self._active_tab
                detail_nid = self._detail_node_id

            # Fetch data relevant to current tab (plus always fetch status)
            status = self._client.server_status()
            health = self._client.health_check()
            sources = self._client.sources()
            perf = self._client.perf_stats()
            mqtt = self._client.mqtt_stats()

            new_cache: Dict[str, Any] = {
                "status": status,
                "health": health,
                "sources": sources,
                "perf": perf,
                "mqtt": mqtt,
            }
            if tab == 0:  # Dashboard
                new_cache["node_health_summary"] = self._client.node_health_summary()
                new_cache["node_states_summary"] = self._client.node_states_summary()
                new_cache["alert_summary"] = self._client.alert_summary()
                new_cache["analytics_summary"] = self._client.analytics_summary()
            elif tab == 1:  # Nodes
                new_cache["nodes"] = self._client.nodes_geojson()
                new_cache["all_node_health"] = self._client.all_node_health()
                new_cache["all_node_states"] = self._client.all_node_states()
                # Fetch detail data if drilling into a node
                if detail_nid:
                    new_cache["detail_health"] = self._client.node_health(detail_nid)
                    new_cache["detail_history"] = self._client.node_history(detail_nid)
                    new_cache["detail_alerts"] = self._client.node_alerts(detail_nid)
                    new_cache["config_drift"] = self._client.config_drift()
            elif tab == 2:  # Alerts
                new_cache["alerts"] = self._client.alerts()
                new_cache["active_alerts"] = self._client.active_alerts()
                new_cache["alert_rules"] = self._client.alert_rules()
            elif tab == 3:  # Propagation
                new_cache["hamclock"] = self._client.hamclock()
            elif tab == 4:  # Topology
                new_cache["topo"] = self._client.topology_geojson()
                new_cache["nodes"] = self._client.nodes_geojson()

            with self._data_lock:
                self._cache.update(new_cache)
                self._connected = True
                self._error_msg = ""
                self._last_refresh = time.time()

        except Exception as e:
            with self._data_lock:
                self._connected = False
                self._error_msg = str(e)

    def _handle_input(self) -> None:
        """Process keyboard input."""
        try:
            key = self._stdscr.getch()
        except curses.error:
            return

        if key == -1:
            return

        # ── Search input mode ──
        if self._search_active:
            if key == 27:  # Escape: cancel search
                self._search_active = False
                self._search_query = ""
                return
            if key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
                # Accept search and exit input mode
                self._search_active = False
                return
            if key in (curses.KEY_BACKSPACE, 127, 8):
                self._search_query = self._search_query[:-1]
                return
            if 32 <= key <= 126:
                self._search_query += chr(key)
                return
            return  # Ignore other keys during search input

        # Quit (q exits detail view first, then app)
        if key in (ord("q"), ord("Q")):
            if self._detail_node_id:
                self._detail_node_id = None
                self._detail_scroll = 0
                return
            self._running = False
            return

        # Escape exits detail view
        if key == 27:
            if self._detail_node_id:
                self._detail_node_id = None
                self._detail_scroll = 0
                return

        # Tab switching by number (1-6)
        tab_keys = [ord("1"), ord("2"), ord("3"), ord("4"), ord("5"), ord("6")]
        if key in tab_keys:
            new_tab = tab_keys.index(key)
            if new_tab < len(self.TAB_NAMES) and new_tab != self._active_tab:
                self._detail_node_id = None  # exit detail view on tab switch
                self._detail_scroll = 0
                self._active_tab = new_tab
                self._refresh_data()
            return

        # Tab switching by arrow / tab key
        if key == ord("\t") or key == curses.KEY_RIGHT:
            self._detail_node_id = None
            self._detail_scroll = 0
            self._active_tab = (self._active_tab + 1) % len(self.TAB_NAMES)
            self._refresh_data()
            return
        if key == curses.KEY_BTAB or key == curses.KEY_LEFT:
            self._detail_node_id = None
            self._detail_scroll = 0
            self._active_tab = (self._active_tab - 1) % len(self.TAB_NAMES)
            self._refresh_data()
            return

        # In node detail view, scroll the detail
        if self._detail_node_id and self._active_tab == 1:
            if key == curses.KEY_DOWN or key == ord("j"):
                self._detail_scroll += 1
                return
            if key == curses.KEY_UP or key == ord("k"):
                self._detail_scroll = max(0, self._detail_scroll - 1)
                return
            if key == curses.KEY_PPAGE:
                self._detail_scroll = max(0, self._detail_scroll - 20)
                return
            if key == curses.KEY_NPAGE:
                self._detail_scroll += 20
                return
            if key == curses.KEY_HOME or key == ord("g"):
                self._detail_scroll = 0
                return
            if key in (ord("r"), ord("R")):
                self._refresh_data()
                return
            return

        # Scroll (normal tab scrolling)
        if key == curses.KEY_DOWN or key == ord("j"):
            if self._active_tab == 1 and not self._detail_node_id:
                self._node_cursor += 1
            self._scroll[self._active_tab] += 1
            return
        if key == curses.KEY_UP or key == ord("k"):
            if self._active_tab == 1 and not self._detail_node_id:
                self._node_cursor = max(0, self._node_cursor - 1)
            self._scroll[self._active_tab] = max(0, self._scroll[self._active_tab] - 1)
            return
        if key == curses.KEY_PPAGE:  # Page Up
            if self._active_tab == 1 and not self._detail_node_id:
                self._node_cursor = max(0, self._node_cursor - 20)
            self._scroll[self._active_tab] = max(0, self._scroll[self._active_tab] - 20)
            return
        if key == curses.KEY_NPAGE:  # Page Down
            if self._active_tab == 1 and not self._detail_node_id:
                self._node_cursor += 20
            self._scroll[self._active_tab] += 20
            return
        if key == curses.KEY_HOME or key == ord("g"):
            if self._active_tab == 1 and not self._detail_node_id:
                self._node_cursor = 0
            self._scroll[self._active_tab] = 0
            return

        # Enter: drill into node detail (Nodes tab)
        if key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
            if self._active_tab == 1 and not self._detail_node_id:
                self._enter_node_detail()
                return

        # Manual refresh
        if key in (ord("r"), ord("R")):
            self._refresh_data()
            return

        # Node sort toggle (Nodes tab, not in detail view)
        if key == ord("s") and self._active_tab == 1 and not self._detail_node_id:
            col, rev = self._node_sort
            self._node_sort = (col, not rev)
            return
        if key == ord("S") and self._active_tab == 1 and not self._detail_node_id:
            col, rev = self._node_sort
            self._node_sort = ((col + 1) % 5, False)
            return

        # / : activate search
        if key == ord("/"):
            self._search_active = True
            self._search_query = ""
            return

        # Escape: clear search filter (when not in search input mode)
        if key == 27 and self._search_query:
            self._search_query = ""
            return

        # Events tab keybindings: p=pause, f=filter type
        if self._active_tab == 5:
            if key == ord("p"):
                self._events_paused = not self._events_paused
                if self._events_paused:
                    with self._data_lock:
                        self._events_paused_snapshot = list(self._event_log)
                return
            if key == ord("f"):
                self._event_type_filter_idx = (
                    (self._event_type_filter_idx + 1)
                    % len(self._event_type_options)
                )
                self._event_type_filter = self._event_type_options[
                    self._event_type_filter_idx
                ]
                return

    def _enter_node_detail(self) -> None:
        """Enter node detail view for the node under cursor."""
        with self._data_lock:
            cache = dict(self._cache)
        node_rows = build_node_rows(cache, self._node_sort)
        if 0 <= self._node_cursor < len(node_rows):
            self._detail_node_id = node_rows[self._node_cursor].get("full_id")
            self._detail_scroll = 0
            self._refresh_data()

    def _draw(self) -> None:
        """Render the full TUI frame."""
        self._stdscr.erase()
        rows, cols = self._stdscr.getmaxyx()
        if rows < 5 or cols < 80:
            safe_addstr(self._stdscr, 0, 0, "Terminal too small")
            self._stdscr.refresh()
            return

        self._draw_header(rows, cols)
        self._draw_status_bar(rows, cols)

        # Content area: rows 1 to rows-2
        content_top = 1
        content_height = rows - 2
        if content_height < 1:
            self._stdscr.refresh()
            return

        with self._data_lock:
            cache = dict(self._cache)
            connected = self._connected
            error_msg = self._error_msg

        if not connected:
            msg = f"Not connected: {error_msg}" if error_msg else "Connecting..."
            safe_addstr(self._stdscr, content_top + 1, 2, msg,
                        curses.color_pair(CP_ALERT_WARNING))
            safe_addstr(self._stdscr, content_top + 3, 2,
                        f"Trying {self._client.base_url} ...")
            safe_addstr(self._stdscr, content_top + 4, 2,
                        "Press 'r' to retry, 'q' to quit.")
        else:
            tab = self._active_tab
            if tab == 0:
                self._draw_dashboard(content_top, content_height, cols, cache)
            elif tab == 1:
                if self._detail_node_id:
                    self._draw_node_detail(content_top, content_height, cols, cache)
                else:
                    self._draw_nodes(content_top, content_height, cols, cache)
            elif tab == 2:
                self._draw_alerts(content_top, content_height, cols, cache)
            elif tab == 3:
                self._draw_propagation(content_top, content_height, cols, cache)
            elif tab == 4:
                self._draw_topology(content_top, content_height, cols, cache)
            elif tab == 5:
                self._draw_events(content_top, content_height, cols)

        self._stdscr.refresh()

    def _draw_header(self, rows: int, cols: int) -> None:
        """Draw the top header bar with tab selectors."""
        attr = curses.color_pair(CP_HEADER) | curses.A_BOLD
        self._stdscr.attron(attr)
        safe_addstr(self._stdscr, 0, 0, " " * cols, attr)
        safe_addstr(self._stdscr, 0, 1, "MeshForge Maps", attr)
        self._stdscr.attroff(attr)

        # Draw tabs
        x = 18
        for i, name in enumerate(self.TAB_NAMES):
            label = f" [{i+1}]{name} "
            if i == self._active_tab:
                ta = curses.color_pair(CP_TAB_ACTIVE) | curses.A_BOLD | curses.A_REVERSE
            else:
                ta = curses.color_pair(CP_HEADER)
            safe_addstr(self._stdscr, 0, x, label, ta)
            x += len(label)

    def _draw_status_bar(self, rows: int, cols: int) -> None:
        """Draw the bottom status bar."""
        attr = curses.color_pair(CP_STATUS_BAR)
        y = rows - 1
        safe_addstr(self._stdscr, y, 0, " " * cols, attr)

        # Left: connection status + last refresh
        with self._data_lock:
            connected = self._connected
            last_ref = self._last_refresh

        if connected:
            status = "CONNECTED"
            sa = attr | curses.A_BOLD
        else:
            status = "DISCONNECTED"
            sa = attr | curses.A_BOLD
        safe_addstr(self._stdscr, y, 1, status, sa)

        if last_ref > 0:
            ago = int(time.time() - last_ref)
            safe_addstr(self._stdscr, y, len(status) + 3,
                        f"Updated {ago}s ago", attr)

        # WebSocket indicator
        with self._data_lock:
            ws_on = self._ws_connected
        ws_str = "  WS:ON" if ws_on else ""
        if ws_str:
            safe_addstr(self._stdscr, y, len(status) + 3 + 16,
                        ws_str, attr | curses.A_BOLD)

        # Search bar display
        if self._search_active:
            search_str = f"  Search: {self._search_query}_"
            safe_addstr(self._stdscr, y, len(status) + 3 + 20,
                        search_str, attr | curses.A_BOLD)
        elif self._search_query:
            filter_str = f"  Filter: {self._search_query}  [Esc]clear"
            safe_addstr(self._stdscr, y, len(status) + 3 + 20,
                        filter_str, attr)

        # Right: keybindings hint
        hint = "q:Quit  r:Refresh  /:Search  1-6:Tab  j/k:Scroll"
        safe_addstr(self._stdscr, y, cols - len(hint) - 2, hint, attr)

    # ── Tab drawing delegates ─────────────────────────────────────

    def _draw_dashboard(self, top: int, height: int, cols: int,
                        cache: Dict[str, Any]) -> None:
        draw_dashboard(self._stdscr, top, height, cols, cache,
                       self._scroll[0])

    def _build_node_rows(self, cache: Dict[str, Any]) -> List[Dict[str, Any]]:
        return build_node_rows(cache, self._node_sort)

    def _draw_nodes(self, top: int, height: int, cols: int,
                    cache: Dict[str, Any]) -> None:
        new_scroll, new_cursor = draw_nodes(
            self._stdscr, top, height, cols, cache,
            self._scroll[1], self._search_query,
            self._node_cursor, self._node_sort)
        self._scroll[1] = new_scroll
        self._node_cursor = new_cursor

    def _draw_node_detail(self, top: int, height: int, cols: int,
                          cache: Dict[str, Any]) -> None:
        draw_node_detail(self._stdscr, top, height, cols, cache,
                         self._detail_scroll,
                         self._detail_node_id or "?",
                         self._node_sort)

    def _draw_alerts(self, top: int, height: int, cols: int,
                     cache: Dict[str, Any]) -> None:
        draw_alerts(self._stdscr, top, height, cols, cache,
                    self._scroll[2], self._search_query)

    def _draw_propagation(self, top: int, height: int, cols: int,
                          cache: Dict[str, Any]) -> None:
        draw_propagation(self._stdscr, top, height, cols, cache,
                         self._scroll[3])

    def _draw_topology(self, top: int, height: int, cols: int,
                       cache: Dict[str, Any]) -> None:
        draw_topology(self._stdscr, top, height, cols, cache,
                      self._scroll[4])

    def _draw_events(self, top: int, height: int, cols: int) -> None:
        with self._data_lock:
            ws_connected = self._ws_connected
            if self._events_paused:
                events = list(self._events_paused_snapshot)
            else:
                events = list(self._event_log)

        draw_events(self._stdscr, top, height, cols,
                    self._scroll[5], events, ws_connected,
                    self._events_paused, self._event_type_filter,
                    self._search_query)

    # ── WebSocket Client ──────────────────────────────────────────

    # Maximum frame payload size to accept (16 MB) -- prevents OOM from
    # malformed frame headers.
    _WS_MAX_FRAME_SIZE = 16 * 1024 * 1024

    def _resolve_ws_endpoint(self) -> Optional[tuple]:
        """Resolve WebSocket host and port from server status API.

        Returns (host, port) or None if unavailable.
        """
        status = self._client.server_status()
        if not status:
            return None

        ws_info = status.get("websocket", {})
        ws_port = ws_info.get("port", status.get("ws_port", 0))
        if not ws_port:
            ws_port = self._client._base.split(":")[-1]
            try:
                ws_port = int(ws_port) + 1
            except (ValueError, TypeError):
                ws_port = 8809

        host = self._client._base.split("//")[-1].split(":")[0]
        return (host, int(ws_port))

    def _ws_listen_loop(self) -> None:
        """Background thread: connect to WebSocket and receive push events.

        Prefers the ``websockets`` library for a standards-compliant client.
        Falls back to a minimal raw-socket implementation if unavailable.
        """
        try:
            import asyncio as _asyncio
            import websockets as _ws
            self._ws_listen_loop_library(_asyncio, _ws)
        except ImportError:
            self._ws_listen_loop_raw()

    def _ws_listen_loop_library(self, _asyncio: Any, _ws: Any) -> None:
        """WebSocket listener using the ``websockets`` library."""
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)

        async def _listen() -> None:
            while self._running:
                try:
                    endpoint = self._resolve_ws_endpoint()
                    if not endpoint:
                        await _asyncio.sleep(5)
                        continue

                    host, port = endpoint
                    uri = f"ws://{host}:{port}/"
                    async with _ws.connect(
                        uri,
                        max_size=self._WS_MAX_FRAME_SIZE,
                        close_timeout=5,
                    ) as conn:
                        with self._data_lock:
                            self._ws_connected = True
                        async for raw in conn:
                            if not self._running:
                                break
                            try:
                                msg = json.loads(raw)
                                self._on_ws_message(msg)
                            except (ValueError, TypeError):
                                pass
                except Exception:
                    pass
                finally:
                    with self._data_lock:
                        self._ws_connected = False
                if self._running:
                    await _asyncio.sleep(5)

        try:
            loop.run_until_complete(_listen())
        except Exception:
            pass
        finally:
            loop.close()

    def _ws_listen_loop_raw(self) -> None:
        """Fallback raw-socket WebSocket listener (stdlib only)."""
        while self._running:
            sock = None
            try:
                endpoint = self._resolve_ws_endpoint()
                if not endpoint:
                    time.sleep(5)
                    continue

                host, port = endpoint

                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10)
                sock.connect((host, port))

                # RFC 6455 handshake
                ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
                handshake = (
                    f"GET / HTTP/1.1\r\n"
                    f"Host: {host}:{port}\r\n"
                    f"Upgrade: websocket\r\n"
                    f"Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {ws_key}\r\n"
                    f"Sec-WebSocket-Version: 13\r\n"
                    f"\r\n"
                )
                sock.sendall(handshake.encode("utf-8"))

                response = b""
                while b"\r\n\r\n" not in response:
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("WS handshake failed")
                    response += chunk

                if b"101" not in response.split(b"\r\n")[0]:
                    raise ConnectionError("WS upgrade rejected")

                with self._data_lock:
                    self._ws_connected = True

                sock.settimeout(30)

                while self._running:
                    frame_data = self._ws_read_frame(sock)
                    if frame_data is None:
                        break
                    try:
                        msg = json.loads(frame_data)
                        self._on_ws_message(msg)
                    except (ValueError, TypeError):
                        pass

            except (OSError, ConnectionError, socket.timeout):
                pass
            finally:
                with self._data_lock:
                    self._ws_connected = False
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

            if self._running:
                time.sleep(5)

    def _ws_read_frame(self, sock: Any) -> Optional[str]:
        """Read a single WebSocket text frame. Returns None on close/error."""

        def _recv_exact(s: Any, n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                chunk = s.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError("Connection closed")
                buf += chunk
            return buf

        try:
            header = _recv_exact(sock, 2)
            opcode = header[0] & 0x0F
            if opcode == 0x8:  # Close frame
                return None
            if opcode == 0x9:  # Ping -> send Pong
                length = header[1] & 0x7F
                if length == 126:
                    length = struct.unpack("!H", _recv_exact(sock, 2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", _recv_exact(sock, 8))[0]
                if length > self._WS_MAX_FRAME_SIZE:
                    return None  # Reject oversized frames
                payload = _recv_exact(sock, length) if length > 0 else b""
                # Send masked pong (RFC 6455 requires client-to-server masking)
                mask = os.urandom(4)
                masked_payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                frame = bytes([0x8A, 0x80 | len(payload)]) + mask + masked_payload
                sock.sendall(frame)
                return ""

            masked = (header[1] & 0x80) != 0
            length = header[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", _recv_exact(sock, 2))[0]
            elif length == 127:
                length = struct.unpack("!Q", _recv_exact(sock, 8))[0]

            if length > self._WS_MAX_FRAME_SIZE:
                return None  # Reject oversized frames

            if masked:
                mask_key = _recv_exact(sock, 4)
                raw = _recv_exact(sock, length)
                data = bytes(b ^ mask_key[i % 4] for i, b in enumerate(raw))
            else:
                data = _recv_exact(sock, length)

            if opcode == 0x1:  # Text frame
                return data.decode("utf-8")
            return None
        except (OSError, ConnectionError, struct.error):
            return None

    def _on_ws_message(self, msg: Dict[str, Any]) -> None:
        """Handle an incoming WebSocket message — add to event log and update cache."""
        with self._data_lock:
            self._event_log.append(msg)
            if len(self._event_log) > self._event_log_max:
                self._event_log = self._event_log[-self._event_log_max:]


def run_tui(host: str = "127.0.0.1", port: int = 8808) -> None:
    """Entry point for launching the TUI."""
    app = TuiApp(host, port)
    app.run()
