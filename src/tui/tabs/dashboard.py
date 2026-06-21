"""Dashboard tab — server status, source health, node counts, perf stats."""

import curses
from typing import Any, Dict, List, Tuple

from ..helpers import (
    CP_SOURCE_OFFLINE,
    CP_SOURCE_ONLINE,
    CP_ALERT_WARNING,
    health_color,
    safe_addstr,
    safe_num,
    severity_color,
)


def draw_dashboard(win: Any, top: int, height: int, cols: int,
                   cache: Dict[str, Any], scroll: int) -> None:
    """Render the Dashboard tab content."""
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
                node_count = safe_num(src_info, "node_count")
                if enabled and available:
                    indicator = "ON "
                    ca = curses.color_pair(CP_SOURCE_ONLINE) | curses.A_BOLD
                elif enabled:
                    indicator = "ERR"
                    ca = curses.color_pair(CP_ALERT_WARNING) | curses.A_BOLD
                else:
                    indicator = "OFF"
                    ca = curses.color_pair(CP_SOURCE_OFFLINE)
                lines.append((f"  [{indicator}] {src_name:<14} {node_count:>4.0f} nodes", ca))
            else:
                lines.append((f"  {src_name}: {src_info}", 0))
    lines.append(("", 0))

    # Node health summary
    nh = cache.get("node_health_summary") or {}
    lines.append((" NODE HEALTH SUMMARY", curses.A_BOLD | curses.A_UNDERLINE))
    dist = nh.get("distribution", nh.get("score_distribution", {}))
    total_nodes = safe_num(nh, "total_nodes", "total_scored")
    avg_score = safe_num(nh, "average_score", "avg_score")
    lines.append((f"  Total: {total_nodes:.0f}   Avg Score: {avg_score:.0f}/100", 0))
    if dist:
        for label in ("excellent", "good", "fair", "poor", "critical"):
            count = safe_num(dist, label)
            pct = (count / total_nodes * 100) if total_nodes > 0 else 0
            lines.append((f"  {label:<10} {count:>4.0f} ({pct:5.1f}%)", health_color(label)))
    lines.append(("", 0))

    # Node state summary
    ns = cache.get("node_states_summary") or {}
    lines.append((" NODE CONNECTIVITY", curses.A_BOLD | curses.A_UNDERLINE))
    states_dist = ns.get("distribution", ns.get("state_distribution", {}))
    for state_name in ("stable", "new", "intermittent", "offline"):
        count = safe_num(states_dist, state_name)
        lines.append((f"  {state_name:<14} {count:>4.0f}", 0))
    lines.append(("", 0))

    # Alert summary
    al = cache.get("alert_summary") or {}
    lines.append((" ALERTS", curses.A_BOLD | curses.A_UNDERLINE))
    total_alerts = safe_num(al, "total", "total_alerts")
    active_count = safe_num(al, "active", "active_count")
    by_severity = al.get("by_severity", {})
    lines.append((f"  Total: {total_alerts:.0f}   Active: {active_count:.0f}", 0))
    for sev in ("critical", "warning", "info"):
        count = safe_num(by_severity, sev)
        lines.append((f"  {sev:<10} {count:>4.0f}", severity_color(sev)))
    lines.append(("", 0))

    # MQTT stats
    mq = cache.get("mqtt") or {}
    if mq:
        lines.append((" MQTT SUBSCRIBER", curses.A_BOLD | curses.A_UNDERLINE))
        running = mq.get("running", False)
        total_msgs = safe_num(mq, "messages_total", "total_messages")
        nodes_tracked = safe_num(mq, "nodes_tracked", "unique_nodes")
        state_str = "running" if running else "stopped"
        sc = curses.color_pair(CP_SOURCE_ONLINE if running else CP_SOURCE_OFFLINE)
        lines.append((f"  State: {state_str}   Messages: {total_msgs:.0f}   "
                      f"Nodes tracked: {nodes_tracked:.0f}", sc))
        lines.append(("", 0))

    # Perf stats
    perf = cache.get("perf") or {}
    if perf:
        lines.append((" PERFORMANCE", curses.A_BOLD | curses.A_UNDERLINE))
        cache_stats = perf.get("cache", {})
        if cache_stats:
            hits = safe_num(cache_stats, "hits")
            misses = safe_num(cache_stats, "misses")
            ratio = safe_num(cache_stats, "hit_ratio")
            lines.append((f"  Cache: {hits:.0f} hits / {misses:.0f} misses "
                          f"({ratio:.0%} hit rate)", 0))
        latency = perf.get("latency", perf.get("request_latency", {}))
        if latency:
            avg_lat = safe_num(latency, "avg", "avg_ms")
            p99_lat = safe_num(latency, "p99", "p99_ms")
            lines.append((f"  Latency: avg={avg_lat:.0f}ms  p99={p99_lat:.0f}ms", 0))

    # Render scrolled lines
    visible = lines[scroll:scroll + height]
    for i, (text, attr) in enumerate(visible):
        safe_addstr(win, top + i, 0, text, attr, cols)
