"""Alerts tab — live alert feed with severity coloring."""

import curses
from typing import Any, Dict, List, Tuple

from ..helpers import (
    CP_HIGHLIGHT,
    CP_SOURCE_OFFLINE,
    CP_SOURCE_ONLINE,
    _format_ts,
    safe_addstr,
    severity_color,
)


def draw_alerts(win: Any, top: int, height: int, cols: int,
                cache: Dict[str, Any], scroll: int,
                search_query: str) -> None:
    """Render the Alerts tab content."""
    alerts_data = cache.get("alerts") or {}
    alert_list = alerts_data if isinstance(alerts_data, list) else alerts_data.get("alerts", [])
    rules_data = cache.get("alert_rules") or {}
    active_data = cache.get("active_alerts") or {}
    active_list = active_data if isinstance(active_data, list) else active_data.get("alerts", [])

    # Apply search filter to alerts
    if search_query:
        q = search_query.lower()
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
