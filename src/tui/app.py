"""MeshForge Maps TUI - Core Application

Curses-based terminal UI with tabbed panels for monitoring mesh network state.
Uses only Python stdlib (curses). Connects to a running MapServer via HTTP API.

Tabs:
  [1] Dashboard  - Server status, source health, node counts, perf stats
  [2] Nodes      - Scrollable node table with health scores and state
  [3] Alerts     - Live alert feed with severity coloring
  [4] Propagation - HF band conditions, space weather, DX spots
"""

import curses
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .data_client import MapDataClient

logger = logging.getLogger(__name__)

# Refresh interval for background data fetch (seconds)
REFRESH_INTERVAL = 5

# Color pair IDs
CP_NORMAL = 0
CP_HEADER = 1
CP_STATUS_BAR = 2
CP_TAB_ACTIVE = 3
CP_TAB_INACTIVE = 4
CP_HEALTH_EXCELLENT = 5
CP_HEALTH_GOOD = 6
CP_HEALTH_FAIR = 7
CP_HEALTH_POOR = 8
CP_HEALTH_CRITICAL = 9
CP_ALERT_INFO = 10
CP_ALERT_WARNING = 11
CP_ALERT_CRITICAL = 12
CP_SOURCE_ONLINE = 13
CP_SOURCE_OFFLINE = 14
CP_HIGHLIGHT = 15
CP_DIM = 16


def _init_colors() -> None:
    """Set up curses color pairs."""
    curses.start_color()
    curses.use_default_colors()

    curses.init_pair(CP_HEADER, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(CP_STATUS_BAR, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(CP_TAB_ACTIVE, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(CP_TAB_INACTIVE, curses.COLOR_CYAN, -1)
    curses.init_pair(CP_HEALTH_EXCELLENT, curses.COLOR_GREEN, -1)
    curses.init_pair(CP_HEALTH_GOOD, curses.COLOR_GREEN, -1)
    curses.init_pair(CP_HEALTH_FAIR, curses.COLOR_YELLOW, -1)
    curses.init_pair(CP_HEALTH_POOR, curses.COLOR_RED, -1)
    curses.init_pair(CP_HEALTH_CRITICAL, curses.COLOR_RED, -1)
    curses.init_pair(CP_ALERT_INFO, curses.COLOR_CYAN, -1)
    curses.init_pair(CP_ALERT_WARNING, curses.COLOR_YELLOW, -1)
    curses.init_pair(CP_ALERT_CRITICAL, curses.COLOR_RED, -1)
    curses.init_pair(CP_SOURCE_ONLINE, curses.COLOR_GREEN, -1)
    curses.init_pair(CP_SOURCE_OFFLINE, curses.COLOR_RED, -1)
    curses.init_pair(CP_HIGHLIGHT, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(CP_DIM, curses.COLOR_WHITE, -1)


def health_color(label: str) -> int:
    """Map health label to color pair."""
    mapping = {
        "excellent": CP_HEALTH_EXCELLENT,
        "good": CP_HEALTH_GOOD,
        "fair": CP_HEALTH_FAIR,
        "poor": CP_HEALTH_POOR,
        "critical": CP_HEALTH_CRITICAL,
    }
    return curses.color_pair(mapping.get(label, CP_NORMAL))


def severity_color(severity: str) -> int:
    """Map alert severity to color pair."""
    mapping = {
        "info": CP_ALERT_INFO,
        "warning": CP_ALERT_WARNING,
        "critical": CP_ALERT_CRITICAL,
    }
    return curses.color_pair(mapping.get(severity, CP_NORMAL))


def safe_addstr(win: Any, y: int, x: int, text: str,
                attr: int = 0, max_width: int = 0) -> None:
    """Write text to curses window, clipping to avoid curses errors."""
    rows, cols = win.getmaxyx()
    if y < 0 or y >= rows or x >= cols:
        return
    available = cols - x - 1  # leave 1 col margin to avoid bottom-right corner issue
    if max_width > 0:
        available = min(available, max_width)
    if available <= 0:
        return
    clipped = text[:available]
    try:
        win.addstr(y, x, clipped, attr)
    except curses.error:
        pass


def draw_hbar(win: Any, y: int, x: int, value: float, width: int,
              filled_attr: int, empty_attr: int = 0) -> None:
    """Draw a horizontal bar gauge."""
    rows, cols = win.getmaxyx()
    if y >= rows or x >= cols:
        return
    width = min(width, cols - x - 1)
    filled = int(value / 100.0 * width)
    filled = max(0, min(filled, width))
    try:
        if filled > 0:
            win.addstr(y, x, "\u2588" * filled, filled_attr)
        empty = width - filled
        if empty > 0:
            win.addstr(y, x + filled, "\u2591" * empty, empty_attr or curses.A_DIM)
    except curses.error:
        pass


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

            # Tab-specific data
            tab = self._active_tab
            if tab == 0:  # Dashboard
                new_cache["node_health_summary"] = self._client.node_health_summary()
                new_cache["node_states_summary"] = self._client.node_states_summary()
                new_cache["alert_summary"] = self._client.alert_summary()
                new_cache["analytics_summary"] = self._client.analytics_summary()
                new_cache["core_health"] = self._client.circuit_breaker_states()
            elif tab == 1:  # Nodes
                new_cache["nodes"] = self._client.nodes_geojson()
                new_cache["all_node_health"] = self._client.all_node_health()
                new_cache["all_node_states"] = self._client.all_node_states()
                # Fetch detail data if drilling into a node
                nid = self._detail_node_id
                if nid:
                    new_cache["detail_health"] = self._client.node_health(nid)
                    new_cache["detail_history"] = self._client.node_history(nid)
                    new_cache["detail_alerts"] = self._client.node_alerts(nid)
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
        node_rows = self._build_node_rows(cache)
        if 0 <= self._node_cursor < len(node_rows):
            self._detail_node_id = node_rows[self._node_cursor].get("full_id")
            self._detail_scroll = 0
            self._refresh_data()

    def _draw(self) -> None:
        """Render the full TUI frame."""
        self._stdscr.erase()
        rows, cols = self._stdscr.getmaxyx()
        if rows < 5 or cols < 40:
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

    # ── Dashboard Tab ──────────────────────────────────────────────

    def _draw_dashboard(self, top: int, height: int, cols: int,
                        cache: Dict[str, Any]) -> None:
        y = top
        win = self._stdscr
        scroll = self._scroll[0]

        lines: List[Tuple[str, int]] = []
        lines.append(("", 0))

        # Server status
        status = cache.get("status") or {}
        uptime = status.get("uptime", "N/A")
        port = status.get("port", "?")
        version = status.get("version", "?")
        lines.append((" SERVER STATUS", curses.A_BOLD | curses.A_UNDERLINE))
        lines.append((f"  Version: {version}    Port: {port}    Uptime: {uptime}", 0))
        lines.append(("", 0))

        # Source health
        sources = cache.get("sources") or {}
        lines.append((" DATA SOURCES", curses.A_BOLD | curses.A_UNDERLINE))
        if isinstance(sources, dict):
            for src_name, src_info in sources.items():
                if isinstance(src_info, dict):
                    enabled = src_info.get("enabled", False)
                    available = src_info.get("available", False)
                    node_count = src_info.get("node_count", 0)
                    if enabled and available:
                        indicator = "ON "
                        ca = curses.color_pair(CP_SOURCE_ONLINE) | curses.A_BOLD
                    elif enabled:
                        indicator = "ERR"
                        ca = curses.color_pair(CP_ALERT_WARNING) | curses.A_BOLD
                    else:
                        indicator = "OFF"
                        ca = curses.color_pair(CP_SOURCE_OFFLINE)
                    lines.append((f"  [{indicator}] {src_name:<14} {node_count:>4} nodes", ca))
                else:
                    lines.append((f"  {src_name}: {src_info}", 0))
        lines.append(("", 0))

        # Node health summary
        nh = cache.get("node_health_summary") or {}
        lines.append((" NODE HEALTH SUMMARY", curses.A_BOLD | curses.A_UNDERLINE))
        dist = nh.get("distribution", nh.get("score_distribution", {}))
        total_nodes = nh.get("total_nodes", nh.get("total_scored", 0))
        avg_score = nh.get("average_score", nh.get("avg_score", 0))
        lines.append((f"  Total: {total_nodes}   Avg Score: {avg_score:.0f}/100", 0))
        if dist:
            for label in ("excellent", "good", "fair", "poor", "critical"):
                count = dist.get(label, 0)
                bar_w = min(30, cols - 30)
                pct = (count / total_nodes * 100) if total_nodes > 0 else 0
                lines.append((f"  {label:<10} {count:>4} ({pct:5.1f}%)", health_color(label)))
        lines.append(("", 0))

        # Node state summary
        ns = cache.get("node_states_summary") or {}
        lines.append((" NODE CONNECTIVITY", curses.A_BOLD | curses.A_UNDERLINE))
        states_dist = ns.get("distribution", ns.get("state_distribution", {}))
        for state_name in ("stable", "new", "intermittent", "offline"):
            count = states_dist.get(state_name, 0) if states_dist else 0
            lines.append((f"  {state_name:<14} {count:>4}", 0))
        lines.append(("", 0))

        # Alert summary
        al = cache.get("alert_summary") or {}
        lines.append((" ALERTS", curses.A_BOLD | curses.A_UNDERLINE))
        total_alerts = al.get("total", al.get("total_alerts", 0))
        active_count = al.get("active", al.get("active_count", 0))
        by_severity = al.get("by_severity", {})
        lines.append((f"  Total: {total_alerts}   Active: {active_count}", 0))
        for sev in ("critical", "warning", "info"):
            count = by_severity.get(sev, 0)
            lines.append((f"  {sev:<10} {count:>4}", severity_color(sev)))
        lines.append(("", 0))

        # MQTT stats
        mq = cache.get("mqtt") or {}
        if mq:
            lines.append((" MQTT SUBSCRIBER", curses.A_BOLD | curses.A_UNDERLINE))
            running = mq.get("running", False)
            total_msgs = mq.get("messages_total", mq.get("total_messages", 0))
            nodes_tracked = mq.get("nodes_tracked", mq.get("unique_nodes", 0))
            state_str = "running" if running else "stopped"
            sc = curses.color_pair(CP_SOURCE_ONLINE if running else CP_SOURCE_OFFLINE)
            lines.append((f"  State: {state_str}   Messages: {total_msgs}   "
                          f"Nodes tracked: {nodes_tracked}", sc))
            lines.append(("", 0))

        # Perf stats
        perf = cache.get("perf") or {}
        if perf:
            lines.append((" PERFORMANCE", curses.A_BOLD | curses.A_UNDERLINE))
            cache_stats = perf.get("cache", {})
            if cache_stats:
                hits = cache_stats.get("hits", 0)
                misses = cache_stats.get("misses", 0)
                ratio = cache_stats.get("hit_ratio", 0)
                lines.append((f"  Cache: {hits} hits / {misses} misses "
                              f"({ratio:.0%} hit rate)", 0))
            latency = perf.get("latency", perf.get("request_latency", {}))
            if latency:
                avg_lat = latency.get("avg", latency.get("avg_ms", 0))
                p99_lat = latency.get("p99", latency.get("p99_ms", 0))
                lines.append((f"  Latency: avg={avg_lat:.0f}ms  p99={p99_lat:.0f}ms", 0))

        # Render scrolled lines
        visible = lines[scroll:scroll + height]
        for i, (text, attr) in enumerate(visible):
            safe_addstr(win, top + i, 0, text, attr, cols)

    # ── Nodes Tab ──────────────────────────────────────────────────

    def _build_node_rows(self, cache: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Build sorted node row list from cache data."""
        nodes_data = cache.get("nodes") or {}
        features = nodes_data.get("features", [])
        health_data = cache.get("all_node_health") or {}
        states_data = cache.get("all_node_states") or {}

        health_map = {}
        if isinstance(health_data, dict):
            for nid, info in health_data.items():
                if isinstance(info, dict):
                    health_map[nid] = info

        state_map = {}
        if isinstance(states_data, dict):
            nodes_list = states_data.get("nodes", states_data)
            if isinstance(nodes_list, dict):
                for nid, info in nodes_list.items():
                    if isinstance(info, dict):
                        state_map[nid] = info

        node_rows: List[Dict[str, Any]] = []
        for feat in features:
            props = feat.get("properties", {})
            nid = props.get("id", props.get("node_id", "?"))
            name = props.get("name", props.get("long_name", nid))
            source = props.get("source", props.get("network", "?"))
            h = health_map.get(nid, {})
            score = h.get("score", h.get("total_score", -1))
            label = h.get("label", h.get("status", ""))
            s = state_map.get(nid, {})
            state = s.get("state", "?")
            node_rows.append({
                "full_id": str(nid),
                "id": str(nid)[:12],
                "name": str(name)[:18],
                "source": str(source)[:10],
                "score": score,
                "label": label,
                "state": state,
            })

        sort_keys = ["id", "name", "source", "score", "state"]
        sort_col, sort_rev = self._node_sort
        sort_col = min(sort_col, len(sort_keys) - 1)
        sk = sort_keys[sort_col]
        try:
            node_rows.sort(key=lambda r: r.get(sk, ""), reverse=sort_rev)
        except TypeError:
            pass
        return node_rows

    def _draw_nodes(self, top: int, height: int, cols: int,
                    cache: Dict[str, Any]) -> None:
        win = self._stdscr
        scroll = self._scroll[1]

        node_rows = self._build_node_rows(cache)

        # Apply search filter
        if self._search_query:
            q = self._search_query.lower()
            node_rows = [r for r in node_rows
                         if q in r["full_id"].lower()
                         or q in r["name"].lower()
                         or q in r["source"].lower()
                         or q in r["state"].lower()
                         or q in r["label"].lower()]

        # Clamp cursor
        total = len(node_rows)
        if total > 0:
            self._node_cursor = max(0, min(self._node_cursor, total - 1))

        sort_keys = ["id", "name", "source", "score", "state"]
        sort_col = min(self._node_sort[0], len(sort_keys) - 1)
        sort_rev = self._node_sort[1]

        # Header
        filter_hint = f"  Filter: '{self._search_query}'" if self._search_query else ""
        safe_addstr(win, top, 1,
                    f" NODES ({total})  [s]sort  [S]col  Enter:detail  "
                    f"Sort: {sort_keys[sort_col]} {'desc' if sort_rev else 'asc'}"
                    f"{filter_hint}",
                    curses.A_BOLD)

        # Column header
        col_hdr = (f"  {'ID':<14}{'Name':<20}{'Source':<12}"
                   f"{'Score':>6} {'Health':<10}{'State':<12}")
        safe_addstr(win, top + 1, 0, col_hdr,
                    curses.color_pair(CP_HIGHLIGHT))
        safe_addstr(win, top + 1, len(col_hdr),
                    " " * max(0, cols - len(col_hdr)),
                    curses.color_pair(CP_HIGHLIGHT))

        # Auto-scroll to keep cursor visible
        row_start = top + 2
        avail = height - 2
        if self._node_cursor < scroll:
            self._scroll[1] = self._node_cursor
            scroll = self._scroll[1]
        elif self._node_cursor >= scroll + avail:
            self._scroll[1] = self._node_cursor - avail + 1
            scroll = self._scroll[1]

        visible = node_rows[scroll:scroll + avail]
        for i, nr in enumerate(visible):
            y = row_start + i
            abs_idx = scroll + i
            is_cursor = (abs_idx == self._node_cursor)
            score = nr["score"]
            label = nr["label"]
            state = nr["state"]

            # Cursor highlight
            if is_cursor:
                safe_addstr(win, y, 0, " " * min(cols - 1, 80),
                            curses.color_pair(CP_HIGHLIGHT))

            cursor_marker = "> " if is_cursor else "  "
            id_str = f"{cursor_marker}{nr['id']:<14}"
            name_str = f"{nr['name']:<20}"
            src_str = f"{nr['source']:<12}"

            row_attr = curses.color_pair(CP_HIGHLIGHT) if is_cursor else 0
            safe_addstr(win, y, 0, id_str, row_attr)
            safe_addstr(win, y, len(id_str), name_str, row_attr)
            safe_addstr(win, y, len(id_str) + len(name_str), src_str, row_attr)

            score_x = len(id_str) + len(name_str) + len(src_str)
            if score >= 0:
                score_str = f"{score:>5.0f} "
                sc_attr = health_color(label)
                if is_cursor:
                    sc_attr = curses.color_pair(CP_HIGHLIGHT)
                safe_addstr(win, y, score_x, score_str, sc_attr)
                safe_addstr(win, y, score_x + len(score_str),
                            f"{label:<10}", sc_attr)
            else:
                safe_addstr(win, y, score_x, "    - ", curses.A_DIM)
                safe_addstr(win, y, score_x + 6, "          ", curses.A_DIM)

            state_x = score_x + 16
            if is_cursor:
                state_attr = curses.color_pair(CP_HIGHLIGHT)
            elif state == "stable":
                state_attr = curses.color_pair(CP_SOURCE_ONLINE)
            elif state == "intermittent":
                state_attr = curses.color_pair(CP_ALERT_WARNING)
            elif state == "offline":
                state_attr = curses.color_pair(CP_SOURCE_OFFLINE)
            else:
                state_attr = 0
            safe_addstr(win, y, state_x, state, state_attr)

        # Scroll indicator
        if total > avail:
            safe_addstr(win, top + height - 1, cols - 20,
                        f"[{scroll+1}-{min(scroll+avail, total)}/{total}]",
                        curses.A_DIM)

    # ── Node Detail View ─────────────────────────────────────────

    def _draw_node_detail(self, top: int, height: int, cols: int,
                          cache: Dict[str, Any]) -> None:
        """Draw detailed view for a single node."""
        win = self._stdscr
        scroll = self._detail_scroll
        nid = self._detail_node_id or "?"

        lines: List[Tuple[str, int]] = []

        # Title bar
        lines.append((f" NODE DETAIL: {nid}  [Esc/q]back  [r]refresh",
                       curses.A_BOLD | curses.A_UNDERLINE))
        lines.append(("", 0))

        # Find basic node info from nodes list
        node_rows = self._build_node_rows(cache)
        node_info = None
        for nr in node_rows:
            if nr.get("full_id") == nid:
                node_info = nr
                break

        if node_info:
            lines.append((" IDENTITY", curses.A_BOLD | curses.A_UNDERLINE))
            lines.append((f"  ID:     {node_info['full_id']}", 0))
            lines.append((f"  Name:   {node_info['name']}", 0))
            lines.append((f"  Source: {node_info['source']}", 0))
            lines.append((f"  State:  {node_info['state']}", 0))
            lines.append(("", 0))

        # Health breakdown
        detail_health = cache.get("detail_health") or {}
        if detail_health:
            lines.append((" HEALTH SCORE", curses.A_BOLD | curses.A_UNDERLINE))
            total_score = detail_health.get("score", detail_health.get("total_score", -1))
            status = detail_health.get("status", detail_health.get("label", "?"))
            lines.append((f"  Overall: {total_score:.0f}/100  ({status})",
                           health_color(status)))

            components = detail_health.get("components", {})
            if components:
                lines.append(("", 0))
                lines.append(("  Component Breakdown:", curses.A_BOLD))
                for comp_name, comp_data in components.items():
                    if isinstance(comp_data, dict):
                        c_score = comp_data.get("score", 0)
                        c_max = comp_data.get("max", 0)
                        pct = (c_score / c_max * 100) if c_max > 0 else 0
                        bar_w = min(20, cols - 45)
                        filled = int(pct / 100 * bar_w) if bar_w > 0 else 0
                        bar = "\u2588" * filled + "\u2591" * (bar_w - filled)
                        detail_parts = []
                        for k, v in comp_data.items():
                            if k not in ("score", "max"):
                                detail_parts.append(f"{k}={v}")
                        detail_str = "  ".join(detail_parts)
                        lines.append((f"  {comp_name:<12} {c_score:>5.1f}/{c_max:<4} "
                                      f"[{bar}]", 0))
                        if detail_str:
                            lines.append((f"    {detail_str}", curses.A_DIM))
            lines.append(("", 0))
        else:
            lines.append((" HEALTH SCORE", curses.A_BOLD | curses.A_UNDERLINE))
            lines.append(("  No health data available for this node.", curses.A_DIM))
            lines.append(("", 0))

        # Observation history
        detail_history = cache.get("detail_history") or {}
        observations = detail_history.get("observations", [])
        lines.append((" RECENT OBSERVATIONS", curses.A_BOLD | curses.A_UNDERLINE))
        if observations:
            obs_hdr = f"  {'Time':<10}{'Lat':>10}{'Lon':>11}{'SNR':>6}{'Batt':>6}{'Network':<12}"
            lines.append((obs_hdr, curses.color_pair(CP_HIGHLIGHT)))
            for obs in observations[:20]:
                ts = obs.get("timestamp", 0)
                time_str = _format_ts(ts)
                lat = obs.get("latitude", 0)
                lon = obs.get("longitude", 0)
                snr = obs.get("snr")
                batt = obs.get("battery")
                net = obs.get("network", "?")
                snr_str = f"{snr:>5.1f}" if snr is not None else "    -"
                batt_str = f"{batt:>4}%" if batt is not None else "    -"
                lines.append((f"  {time_str:<10}{lat:>10.5f}{lon:>11.5f}"
                              f"{snr_str}{batt_str} {net}", 0))
        else:
            lines.append(("  No observation history available.", curses.A_DIM))
        lines.append(("", 0))

        # Config drift for this node
        drift_data = cache.get("config_drift") or {}
        recent_drifts = drift_data.get("recent_drifts", [])
        node_drifts = [d for d in recent_drifts
                       if isinstance(d, dict) and d.get("node_id") == nid]
        lines.append((" CONFIG DRIFT", curses.A_BOLD | curses.A_UNDERLINE))
        if node_drifts:
            for d in node_drifts[:10]:
                field = d.get("field", "?")
                old = d.get("old_value", "?")
                new = d.get("new_value", "?")
                sev = d.get("severity", "info")
                ts = d.get("timestamp", 0)
                time_str = _format_ts(ts)
                lines.append((f"  [{sev.upper():<8}] {field}: {old} -> {new}  ({time_str})",
                               severity_color(sev)))
        else:
            lines.append(("  No configuration changes detected.", curses.A_DIM))
        lines.append(("", 0))

        # Node-specific alerts
        detail_alerts = cache.get("detail_alerts") or {}
        alert_list = detail_alerts if isinstance(detail_alerts, list) else detail_alerts.get("alerts", [])
        lines.append((" NODE ALERTS", curses.A_BOLD | curses.A_UNDERLINE))
        if alert_list:
            for al in alert_list[:15]:
                if isinstance(al, dict):
                    sev = al.get("severity", "info")
                    atype = al.get("alert_type", al.get("type", "?"))
                    msg = al.get("message", "")[:50]
                    ts = al.get("timestamp", 0)
                    time_str = _format_ts(ts)
                    lines.append((f"  [{sev.upper():<8}] {atype:<20} {time_str}  {msg}",
                                   severity_color(sev)))
        else:
            lines.append(("  No alerts for this node.", curses.A_DIM))

        # Render scrolled
        visible = lines[scroll:scroll + height]
        for i, (text, attr) in enumerate(visible):
            safe_addstr(win, top + i, 0, text, attr, cols)

    # ── Alerts Tab ─────────────────────────────────────────────────

    def _draw_alerts(self, top: int, height: int, cols: int,
                     cache: Dict[str, Any]) -> None:
        win = self._stdscr
        scroll = self._scroll[2]

        alerts_data = cache.get("alerts") or {}
        alert_list = alerts_data if isinstance(alerts_data, list) else alerts_data.get("alerts", [])
        rules_data = cache.get("alert_rules") or {}
        active_data = cache.get("active_alerts") or {}
        active_list = active_data if isinstance(active_data, list) else active_data.get("alerts", [])

        # Apply search filter to alerts
        if self._search_query:
            q = self._search_query.lower()
            alert_list = [a for a in alert_list if isinstance(a, dict) and (
                q in a.get("alert_type", "").lower()
                or q in a.get("severity", "").lower()
                or q in a.get("node_id", "").lower()
                or q in a.get("message", "").lower())]
            active_list = [a for a in active_list if isinstance(a, dict) and (
                q in a.get("alert_type", "").lower()
                or q in a.get("severity", "").lower()
                or q in a.get("node_id", "").lower()
                or q in a.get("message", "").lower())]

        lines: List[Tuple[str, int]] = []

        # Summary line
        total = len(alert_list)
        active = len(active_list)
        lines.append((f" ALERTS  Total: {total}  Active: {active}",
                       curses.A_BOLD))
        lines.append(("", 0))

        # Active alerts first
        if active_list:
            lines.append((" ACTIVE ALERTS", curses.A_BOLD | curses.A_UNDERLINE))
            for al in active_list:
                if isinstance(al, dict):
                    sev = al.get("severity", "info")
                    atype = al.get("alert_type", al.get("type", "?"))
                    node = al.get("node_id", "?")
                    msg = al.get("message", al.get("description", ""))
                    ts = al.get("timestamp", 0)
                    time_str = _format_ts(ts)
                    prefix = f"  [{sev.upper():<8}]"
                    detail = f" {atype:<20} node={node:<12} {time_str}"
                    lines.append((prefix + detail, severity_color(sev)))
                    if msg:
                        lines.append((f"    {msg}", curses.A_DIM))
            lines.append(("", 0))

        # Full history
        lines.append((" ALERT HISTORY", curses.A_BOLD | curses.A_UNDERLINE))
        col_hdr = f"  {'Severity':<10}{'Type':<22}{'Node':<14}{'Time':<12}Message"
        lines.append((col_hdr, curses.color_pair(CP_HIGHLIGHT)))

        for al in alert_list:
            if isinstance(al, dict):
                sev = al.get("severity", "info")
                atype = al.get("alert_type", al.get("type", "?"))
                node = al.get("node_id", "?")
                msg = al.get("message", al.get("description", ""))[:40]
                ts = al.get("timestamp", 0)
                time_str = _format_ts(ts)
                row = f"  {sev:<10}{atype:<22}{str(node):<14}{time_str:<12}{msg}"
                lines.append((row, severity_color(sev)))

        if not alert_list:
            lines.append(("  No alerts recorded.", curses.A_DIM))

        lines.append(("", 0))

        # Alert rules
        rules_list = rules_data if isinstance(rules_data, list) else rules_data.get("rules", [])
        if rules_list:
            lines.append((" ALERT RULES", curses.A_BOLD | curses.A_UNDERLINE))
            for rule in rules_list:
                if isinstance(rule, dict):
                    rid = rule.get("rule_id", "?")
                    enabled = rule.get("enabled", True)
                    rtype = rule.get("alert_type", "?")
                    sev = rule.get("severity", "?")
                    metric = rule.get("metric", "?")
                    op = rule.get("operator", "?")
                    thresh = rule.get("threshold", "?")
                    state_str = "ON " if enabled else "OFF"
                    sc = curses.color_pair(
                        CP_SOURCE_ONLINE if enabled else CP_SOURCE_OFFLINE)
                    lines.append((f"  [{state_str}] {rid:<20} {rtype:<18} "
                                  f"{metric} {op} {thresh}  ({sev})", sc))

        # Render
        visible = lines[scroll:scroll + height]
        for i, (text, attr) in enumerate(visible):
            safe_addstr(win, top + i, 0, text, attr, cols)

    # ── Propagation Tab ────────────────────────────────────────────

    def _draw_propagation(self, top: int, height: int, cols: int,
                          cache: Dict[str, Any]) -> None:
        win = self._stdscr
        scroll = self._scroll[3]

        hc = cache.get("hamclock") or {}

        lines: List[Tuple[str, int]] = []
        lines.append(("", 0))

        available = hc.get("available", False)
        source = hc.get("source", "unknown")

        lines.append((" HF PROPAGATION & SPACE WEATHER",
                       curses.A_BOLD | curses.A_UNDERLINE))
        if not available:
            lines.append(("  HamClock data not available.", curses.A_DIM))
            lines.append(("  Ensure OpenHamClock is running or NOAA fallback is enabled.", 0))
            lines.append(("", 0))

        lines.append((f"  Data source: {source}", 0))
        lines.append(("", 0))

        # Space weather
        sw = hc.get("space_weather", {})
        if sw:
            lines.append((" SPACE WEATHER", curses.A_BOLD | curses.A_UNDERLINE))
            sfi = sw.get("solar_flux", "--")
            kp = sw.get("kp_index", "--")
            cond = sw.get("band_conditions", "unknown")
            lines.append((f"  Solar Flux Index (SFI): {sfi}", 0))

            # Kp coloring
            kp_val = 0
            try:
                kp_val = float(kp)
            except (ValueError, TypeError):
                pass
            if kp_val >= 5:
                kp_attr = curses.color_pair(CP_ALERT_CRITICAL) | curses.A_BOLD
            elif kp_val >= 3:
                kp_attr = curses.color_pair(CP_ALERT_WARNING)
            else:
                kp_attr = curses.color_pair(CP_SOURCE_ONLINE)
            lines.append((f"  Kp Index: {kp}", kp_attr))
            lines.append((f"  Band Conditions: {cond}", 0))
            lines.append(("", 0))

        # VOACAP predictions
        voacap = hc.get("voacap", {})
        if voacap and voacap.get("bands"):
            lines.append((" VOACAP BAND PREDICTIONS", curses.A_BOLD | curses.A_UNDERLINE))
            best = voacap.get("best_band", "")
            col_hdr = f"  {'Band':<10}{'Reliability':>12}  {'Status':<12}"
            lines.append((col_hdr, curses.color_pair(CP_HIGHLIGHT)))
            for band, info in voacap["bands"].items():
                if isinstance(info, dict):
                    rel = info.get("reliability", 0)
                    status = info.get("status", "?")
                    marker = " << BEST" if band == best else ""
                    # Color by reliability
                    if rel >= 70:
                        ba = curses.color_pair(CP_HEALTH_EXCELLENT)
                    elif rel >= 40:
                        ba = curses.color_pair(CP_HEALTH_FAIR)
                    else:
                        ba = curses.color_pair(CP_HEALTH_POOR)
                    lines.append((f"  {band:<10}{rel:>10}%  {status:<12}{marker}", ba))
            lines.append(("", 0))

        # Band conditions
        bc = hc.get("band_conditions", {})
        if bc and bc.get("bands"):
            lines.append((" BAND CONDITIONS", curses.A_BOLD | curses.A_UNDERLINE))
            for band, cond_val in bc["bands"].items():
                cond_str = str(cond_val)
                if "good" in cond_str.lower():
                    ca = curses.color_pair(CP_HEALTH_GOOD)
                elif "fair" in cond_str.lower():
                    ca = curses.color_pair(CP_HEALTH_FAIR)
                elif "poor" in cond_str.lower():
                    ca = curses.color_pair(CP_HEALTH_POOR)
                else:
                    ca = 0
                lines.append((f"  {band:<10} {cond_str}", ca))
            lines.append(("", 0))

        # DX spots
        spots = hc.get("dxspots", [])
        if spots:
            lines.append((" DX SPOTS", curses.A_BOLD | curses.A_UNDERLINE))
            col_hdr = f"  {'DX Call':<12}{'Freq kHz':>10}  {'DE Call':<12}{'UTC':<8}"
            lines.append((col_hdr, curses.color_pair(CP_HIGHLIGHT)))
            for s in spots[:20]:
                if isinstance(s, dict):
                    dx = s.get("dx_call", "?")
                    freq = s.get("freq_khz", "?")
                    de = s.get("de_call", "")
                    utc = s.get("utc", "")
                    lines.append((f"  {dx:<12}{str(freq):>10}  {de:<12}{utc:<8}", 0))

        # DE / DX station info
        de = hc.get("de_station", {})
        dx = hc.get("dx_station", {})
        if de or dx:
            lines.append(("", 0))
            lines.append((" STATION INFO", curses.A_BOLD | curses.A_UNDERLINE))
            if de:
                lines.append((f"  DE: {de.get('call', '--')} "
                              f"Grid: {de.get('grid', '--')}", 0))
            if dx:
                lines.append((f"  DX: {dx.get('call', '--')} "
                              f"Grid: {dx.get('grid', '--')}", 0))

        # Render
        visible = lines[scroll:scroll + height]
        for i, (text, attr) in enumerate(visible):
            safe_addstr(win, top + i, 0, text, attr, cols)

    # ── Topology Tab ──────────────────────────────────────────────

    def _draw_topology(self, top: int, height: int, cols: int,
                       cache: Dict[str, Any]) -> None:
        """Draw ASCII topology visualization of mesh links."""
        win = self._stdscr
        scroll = self._scroll[4]

        topo_data = cache.get("topo") or {}
        features = topo_data.get("features", [])
        nodes_data = cache.get("nodes") or {}
        node_features = nodes_data.get("features", [])

        lines: List[Tuple[str, int]] = []
        lines.append(("", 0))

        # Build node name lookup
        node_names: Dict[str, str] = {}
        for feat in node_features:
            props = feat.get("properties", {})
            nid = props.get("id", props.get("node_id", ""))
            name = props.get("name", props.get("long_name", str(nid)[:8]))
            node_names[str(nid)] = str(name)[:12]

        # Build adjacency from topology links
        links: List[Dict[str, Any]] = []
        adjacency: Dict[str, List[Tuple[str, float, str]]] = {}
        for feat in features:
            props = feat.get("properties", {})
            src = str(props.get("source", ""))
            tgt = str(props.get("target", ""))
            snr = props.get("snr", 0)
            quality = props.get("quality", "unknown")
            if src and tgt:
                links.append({"source": src, "target": tgt, "snr": snr,
                              "quality": quality})
                adjacency.setdefault(src, []).append((tgt, snr, quality))
                adjacency.setdefault(tgt, []).append((src, snr, quality))

        link_count = len(links)
        node_count = len(adjacency)

        lines.append((" MESH TOPOLOGY", curses.A_BOLD | curses.A_UNDERLINE))
        lines.append((f"  {node_count} nodes, {link_count} links", 0))
        lines.append(("", 0))

        if not links:
            lines.append(("  No topology data available.", curses.A_DIM))
            lines.append(("  Topology requires active mesh links (MQTT/AREDN).", 0))
        else:
            # Link quality legend
            lines.append((" LINK QUALITY LEGEND", curses.A_BOLD | curses.A_UNDERLINE))
            lines.append(("  === excellent (SNR>=8dB)   --- good (5-8dB)   "
                          "... marginal (0-5dB)   ~~~ poor (<0dB)",
                          curses.A_DIM))
            lines.append(("", 0))

            # ASCII topology: show each node and its neighbors
            lines.append((" ADJACENCY MAP", curses.A_BOLD | curses.A_UNDERLINE))

            # Sort nodes by number of connections (hubs first)
            sorted_nodes = sorted(adjacency.keys(),
                                  key=lambda n: len(adjacency[n]),
                                  reverse=True)

            for nid in sorted_nodes:
                neighbors = adjacency[nid]
                name = node_names.get(nid, str(nid)[:8])
                conn_count = len(neighbors)
                lines.append(("", 0))
                lines.append((f"  [{name}] ({conn_count} links)",
                               curses.A_BOLD))

                for tgt_id, snr, quality in sorted(neighbors,
                                                    key=lambda x: x[1],
                                                    reverse=True):
                    tgt_name = node_names.get(tgt_id, str(tgt_id)[:8])
                    # Pick link style based on quality
                    if quality == "excellent":
                        link_char = "==="
                        la = curses.color_pair(CP_HEALTH_EXCELLENT)
                    elif quality == "good":
                        link_char = "---"
                        la = curses.color_pair(CP_HEALTH_GOOD)
                    elif quality == "marginal":
                        link_char = "..."
                        la = curses.color_pair(CP_HEALTH_FAIR)
                    elif quality in ("poor", "bad"):
                        link_char = "~~~"
                        la = curses.color_pair(CP_HEALTH_POOR)
                    else:
                        link_char = "???"
                        la = curses.A_DIM

                    snr_str = f"{snr:>5.1f}dB" if snr else "   --  "
                    lines.append((f"    {link_char}{link_char} {tgt_name:<14} "
                                  f"SNR:{snr_str}", la))

            lines.append(("", 0))

            # Link table (sorted by SNR)
            lines.append((" ALL LINKS (by SNR)", curses.A_BOLD | curses.A_UNDERLINE))
            link_hdr = f"  {'Source':<14}{'Target':<14}{'SNR':>8}  {'Quality':<12}"
            lines.append((link_hdr, curses.color_pair(CP_HIGHLIGHT)))

            sorted_links = sorted(links, key=lambda l: l.get("snr", 0),
                                  reverse=True)
            for lk in sorted_links:
                src_name = node_names.get(lk["source"], lk["source"][:10])
                tgt_name = node_names.get(lk["target"], lk["target"][:10])
                snr = lk.get("snr", 0)
                quality = lk.get("quality", "?")
                snr_str = f"{snr:>7.1f}" if snr else "     --"
                la = _quality_color(quality)
                lines.append((f"  {src_name:<14}{tgt_name:<14}{snr_str}  "
                              f"{quality:<12}", la))

        # Render scrolled
        visible = lines[scroll:scroll + height]
        for i, (text, attr) in enumerate(visible):
            safe_addstr(win, top + i, 0, text, attr, cols)

    # ── Events Tab ────────────────────────────────────────────────

    def _draw_events(self, top: int, height: int, cols: int) -> None:
        """Draw live event stream from WebSocket."""
        win = self._stdscr
        scroll = self._scroll[5]

        lines: List[Tuple[str, int]] = []
        lines.append(("", 0))

        with self._data_lock:
            ws_connected = self._ws_connected
            if self._events_paused:
                events = list(self._events_paused_snapshot)
            else:
                events = list(self._event_log)

        # Apply type filter
        type_filter = self._event_type_filter
        if type_filter:
            events = [e for e in events
                      if type_filter in e.get("type", "")]

        # Apply search filter
        if self._search_query:
            q = self._search_query.lower()
            events = [e for e in events
                      if q in e.get("type", "").lower()
                      or q in e.get("node_id", "").lower()
                      or q in e.get("source", "").lower()
                      or q in str(e.get("data", "")).lower()]

        lines.append((" EVENT STREAM", curses.A_BOLD | curses.A_UNDERLINE))
        ws_status = "CONNECTED" if ws_connected else "POLLING"
        ws_attr = (curses.color_pair(CP_SOURCE_ONLINE) if ws_connected
                   else curses.color_pair(CP_ALERT_WARNING))
        pause_str = "  PAUSED" if self._events_paused else ""
        filter_str = f"  Type:{type_filter}" if type_filter else ""
        lines.append((f"  WebSocket: {ws_status}   Events: {len(events)}"
                       f"{pause_str}{filter_str}   [p]pause [f]filter",
                       ws_attr))
        lines.append(("", 0))

        if not events:
            lines.append(("  No events received yet.", curses.A_DIM))
            lines.append(("  Events appear here as WebSocket messages arrive.", 0))
        else:
            # Column header
            evt_hdr = f"  {'Time':<10}{'Type':<22}{'Source':<10}{'Node':<14}Detail"
            lines.append((evt_hdr, curses.color_pair(CP_HIGHLIGHT)))

            # Show newest first
            for evt in reversed(events):
                ts = evt.get("timestamp", 0)
                time_str = _format_ts(ts)
                etype = evt.get("type", "?")
                source = evt.get("source", "")[:8]
                node_id = evt.get("node_id", "")[:12]
                # Build detail string from data
                data = evt.get("data", {})
                detail_parts = []
                if isinstance(data, dict):
                    for k in ("battery", "snr", "lat", "lon", "severity",
                              "message"):
                        if k in data:
                            val = data[k]
                            if isinstance(val, float):
                                detail_parts.append(f"{k}={val:.1f}")
                            else:
                                detail_parts.append(f"{k}={val}")
                detail_str = " ".join(detail_parts)[:40]

                ea = _event_type_color(etype)
                lines.append((f"  {time_str:<10}{etype:<22}{source:<10}"
                              f"{node_id:<14}{detail_str}", ea))

        # Render scrolled
        visible = lines[scroll:scroll + height]
        for i, (text, attr) in enumerate(visible):
            safe_addstr(win, top + i, 0, text, attr, cols)

    # ── WebSocket Client ──────────────────────────────────────────

    def _ws_listen_loop(self) -> None:
        """Background thread: connect to WebSocket and receive push events."""
        import hashlib
        import base64
        import socket
        import struct
        import os

        while self._running:
            try:
                # Get WebSocket port from status API
                status = self._client.server_status()
                if not status:
                    time.sleep(5)
                    continue

                ws_info = status.get("websocket", {})
                # Try to find WS port: could be in status.websocket.port or status.ws_port
                ws_port = ws_info.get("port", status.get("ws_port", 0))
                if not ws_port:
                    # Default convention: HTTP port + 1
                    ws_port = self._client._base.split(":")[-1]
                    try:
                        ws_port = int(ws_port) + 1
                    except (ValueError, TypeError):
                        ws_port = 8809

                host = self._client._base.split("//")[-1].split(":")[0]

                # WebSocket handshake
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10)
                sock.connect((host, int(ws_port)))

                ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
                handshake = (
                    f"GET / HTTP/1.1\r\n"
                    f"Host: {host}:{ws_port}\r\n"
                    f"Upgrade: websocket\r\n"
                    f"Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {ws_key}\r\n"
                    f"Sec-WebSocket-Version: 13\r\n"
                    f"\r\n"
                )
                sock.sendall(handshake.encode("utf-8"))

                # Read handshake response
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

                # Read WebSocket frames
                while self._running:
                    frame_data = self._ws_read_frame(sock)
                    if frame_data is None:
                        break
                    try:
                        import json as _json
                        msg = _json.loads(frame_data)
                        self._on_ws_message(msg)
                    except (ValueError, TypeError):
                        pass

            except (OSError, ConnectionError, socket.timeout):
                pass
            finally:
                with self._data_lock:
                    self._ws_connected = False
                try:
                    sock.close()
                except Exception:
                    pass

            # Reconnect backoff
            if self._running:
                time.sleep(5)

    def _ws_read_frame(self, sock: Any) -> Optional[str]:
        """Read a single WebSocket text frame. Returns None on close/error."""
        import struct

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
                payload = _recv_exact(sock, length) if length > 0 else b""
                # Send pong
                pong = bytes([0x8A, len(payload)]) + payload
                sock.sendall(pong)
                return ""  # empty string, not a real message

            masked = (header[1] & 0x80) != 0
            length = header[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", _recv_exact(sock, 2))[0]
            elif length == 127:
                length = struct.unpack("!Q", _recv_exact(sock, 8))[0]

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


def _quality_color(quality: str) -> int:
    """Map topology link quality to color pair."""
    mapping = {
        "excellent": CP_HEALTH_EXCELLENT,
        "good": CP_HEALTH_GOOD,
        "marginal": CP_HEALTH_FAIR,
        "poor": CP_HEALTH_POOR,
        "bad": CP_HEALTH_CRITICAL,
    }
    return curses.color_pair(mapping.get(quality, CP_NORMAL))


def _event_type_color(etype: str) -> int:
    """Map event type to color pair."""
    if "alert" in etype:
        return curses.color_pair(CP_ALERT_CRITICAL)
    if "position" in etype:
        return curses.color_pair(CP_HEALTH_GOOD)
    if "telemetry" in etype:
        return curses.color_pair(CP_ALERT_INFO)
    if "topology" in etype:
        return curses.color_pair(CP_HEALTH_FAIR)
    if "service" in etype:
        return curses.color_pair(CP_ALERT_WARNING)
    return 0


def _format_ts(ts: float) -> str:
    """Format a unix timestamp as HH:MM:SS."""
    if not ts:
        return "--:--:--"
    try:
        return time.strftime("%H:%M:%S", time.localtime(ts))
    except (OSError, ValueError):
        return "??:??:??"


def run_tui(host: str = "127.0.0.1", port: int = 8808) -> None:
    """Entry point for launching the TUI."""
    app = TuiApp(host, port)
    app.run()
