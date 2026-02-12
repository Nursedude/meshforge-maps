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

    TAB_NAMES = ["Dashboard", "Nodes", "Alerts", "Propagation"]

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
            elif tab == 2:  # Alerts
                new_cache["alerts"] = self._client.alerts()
                new_cache["active_alerts"] = self._client.active_alerts()
                new_cache["alert_rules"] = self._client.alert_rules()
            elif tab == 3:  # Propagation
                new_cache["hamclock"] = self._client.hamclock()

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

        # Quit
        if key in (ord("q"), ord("Q")):
            self._running = False
            return

        # Tab switching by number
        if key in (ord("1"), ord("2"), ord("3"), ord("4")):
            new_tab = key - ord("1")
            if new_tab != self._active_tab:
                self._active_tab = new_tab
                self._refresh_data()  # fetch tab-specific data
            return

        # Tab switching by arrow / tab key
        if key == ord("\t") or key == curses.KEY_RIGHT:
            self._active_tab = (self._active_tab + 1) % len(self.TAB_NAMES)
            self._refresh_data()
            return
        if key == curses.KEY_BTAB or key == curses.KEY_LEFT:
            self._active_tab = (self._active_tab - 1) % len(self.TAB_NAMES)
            self._refresh_data()
            return

        # Scroll
        if key == curses.KEY_DOWN or key == ord("j"):
            self._scroll[self._active_tab] += 1
            return
        if key == curses.KEY_UP or key == ord("k"):
            self._scroll[self._active_tab] = max(0, self._scroll[self._active_tab] - 1)
            return
        if key == curses.KEY_PPAGE:  # Page Up
            self._scroll[self._active_tab] = max(0, self._scroll[self._active_tab] - 20)
            return
        if key == curses.KEY_NPAGE:  # Page Down
            self._scroll[self._active_tab] += 20
            return
        if key == curses.KEY_HOME or key == ord("g"):
            self._scroll[self._active_tab] = 0
            return

        # Manual refresh
        if key in (ord("r"), ord("R")):
            self._refresh_data()
            return

        # Node sort toggle (Nodes tab)
        if key == ord("s") and self._active_tab == 1:
            col, rev = self._node_sort
            self._node_sort = (col, not rev)
            return
        if key == ord("S") and self._active_tab == 1:
            col, rev = self._node_sort
            self._node_sort = ((col + 1) % 5, False)
            return

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
                self._draw_nodes(content_top, content_height, cols, cache)
            elif tab == 2:
                self._draw_alerts(content_top, content_height, cols, cache)
            elif tab == 3:
                self._draw_propagation(content_top, content_height, cols, cache)

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

        # Right: keybindings hint
        hint = "q:Quit  r:Refresh  Tab:Switch  j/k:Scroll"
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

    def _draw_nodes(self, top: int, height: int, cols: int,
                    cache: Dict[str, Any]) -> None:
        win = self._stdscr
        scroll = self._scroll[1]

        nodes_data = cache.get("nodes") or {}
        features = nodes_data.get("features", [])
        health_data = cache.get("all_node_health") or {}
        states_data = cache.get("all_node_states") or {}

        # Build a merged node table
        node_rows: List[Dict[str, Any]] = []
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
                "id": str(nid)[:12],
                "name": str(name)[:18],
                "source": str(source)[:10],
                "score": score,
                "label": label,
                "state": state,
            })

        # Sort
        sort_keys = ["id", "name", "source", "score", "state"]
        sort_col, sort_rev = self._node_sort
        sort_col = min(sort_col, len(sort_keys) - 1)
        sk = sort_keys[sort_col]
        try:
            node_rows.sort(key=lambda r: r.get(sk, ""), reverse=sort_rev)
        except TypeError:
            pass

        # Header
        total = len(node_rows)
        safe_addstr(win, top, 1,
                    f" NODES ({total})  [s]toggle sort  [S]next column  "
                    f"Sort: {sort_keys[sort_col]} {'desc' if sort_rev else 'asc'}",
                    curses.A_BOLD)

        # Column header
        col_hdr = (f"  {'ID':<14}{'Name':<20}{'Source':<12}"
                   f"{'Score':>6} {'Health':<10}{'State':<12}")
        safe_addstr(win, top + 1, 0, col_hdr,
                    curses.color_pair(CP_HIGHLIGHT))
        safe_addstr(win, top + 1, len(col_hdr),
                    " " * max(0, cols - len(col_hdr)),
                    curses.color_pair(CP_HIGHLIGHT))

        # Rows
        row_start = top + 2
        avail = height - 2
        visible = node_rows[scroll:scroll + avail]
        for i, nr in enumerate(visible):
            y = row_start + i
            score = nr["score"]
            label = nr["label"]
            state = nr["state"]

            id_str = f"  {nr['id']:<14}"
            name_str = f"{nr['name']:<20}"
            src_str = f"{nr['source']:<12}"

            safe_addstr(win, y, 0, id_str, 0)
            safe_addstr(win, y, len(id_str), name_str, 0)
            safe_addstr(win, y, len(id_str) + len(name_str), src_str, 0)

            score_x = len(id_str) + len(name_str) + len(src_str)
            if score >= 0:
                score_str = f"{score:>5.0f} "
                safe_addstr(win, y, score_x, score_str, health_color(label))
                safe_addstr(win, y, score_x + len(score_str),
                            f"{label:<10}", health_color(label))
            else:
                safe_addstr(win, y, score_x, "    - ", curses.A_DIM)
                safe_addstr(win, y, score_x + 6, "          ", curses.A_DIM)

            state_x = score_x + 16
            state_attr = 0
            if state == "stable":
                state_attr = curses.color_pair(CP_SOURCE_ONLINE)
            elif state == "intermittent":
                state_attr = curses.color_pair(CP_ALERT_WARNING)
            elif state == "offline":
                state_attr = curses.color_pair(CP_SOURCE_OFFLINE)
            safe_addstr(win, y, state_x, state, state_attr)

        # Scroll indicator
        if total > avail:
            pct = (scroll / max(1, total - avail)) * 100
            safe_addstr(win, top + height - 1, cols - 20,
                        f"[{scroll+1}-{min(scroll+avail, total)}/{total}]",
                        curses.A_DIM)

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
