"""Dashboard tab — server status, source health, node counts, perf stats."""

import curses
from typing import Any, Dict, List, Tuple

from ..helpers import (
    CP_SOURCE_OFFLINE,
    CP_SOURCE_ONLINE,
    CP_ALERT_WARNING,
    health_color,
    safe_addstr,
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
