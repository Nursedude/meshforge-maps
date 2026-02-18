"""Nodes tab — scrollable node table with health scores, state, and detail drill-down."""

import curses
from typing import Any, Dict, List, Tuple

from ..helpers import (
    CP_ALERT_WARNING,
    CP_HIGHLIGHT,
    CP_SOURCE_OFFLINE,
    CP_SOURCE_ONLINE,
    _format_ts,
    health_color,
    safe_addstr,
    severity_color,
)


def build_node_rows(cache: Dict[str, Any],
                    node_sort: Tuple[int, bool]) -> List[Dict[str, Any]]:
    """Build sorted node row list from cache data."""
    nodes_data = cache.get("nodes") or {}
    features = nodes_data.get("features", [])
    health_data = cache.get("all_node_health") or {}
    states_data = cache.get("all_node_states") or {}

    health_map: Dict[str, Dict[str, Any]] = {}
    if isinstance(health_data, dict):
        for nid, info in health_data.items():
            if isinstance(info, dict):
                health_map[nid] = info

    state_map: Dict[str, Dict[str, Any]] = {}
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
    sort_col, sort_rev = node_sort
    sort_col = min(sort_col, len(sort_keys) - 1)
    sk = sort_keys[sort_col]
    try:
        node_rows.sort(key=lambda r: r.get(sk, ""), reverse=sort_rev)
    except TypeError:
        pass
    return node_rows


def draw_nodes(win: Any, top: int, height: int, cols: int,
               cache: Dict[str, Any], scroll: int, search_query: str,
               node_cursor: int,
               node_sort: Tuple[int, bool]) -> Tuple[int, int]:
    """Render the Nodes table.

    Returns ``(updated_scroll, clamped_cursor)`` so the caller can write
    them back to the app state.
    """
    node_rows = build_node_rows(cache, node_sort)

    # Apply search filter
    if search_query:
        q = search_query.lower()
        node_rows = [r for r in node_rows
                     if q in r["full_id"].lower()
                     or q in r["name"].lower()
                     or q in r["source"].lower()
                     or q in r["state"].lower()
                     or q in r["label"].lower()]

    # Clamp cursor
    total = len(node_rows)
    if total > 0:
        node_cursor = max(0, min(node_cursor, total - 1))

    sort_keys = ["id", "name", "source", "score", "state"]
    sort_col = min(node_sort[0], len(sort_keys) - 1)
    sort_rev = node_sort[1]

    # Header
    filter_hint = f"  Filter: '{search_query}'" if search_query else ""
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
    if node_cursor < scroll:
        scroll = node_cursor
    elif node_cursor >= scroll + avail:
        scroll = node_cursor - avail + 1

    visible = node_rows[scroll:scroll + avail]
    for i, nr in enumerate(visible):
        y = row_start + i
        abs_idx = scroll + i
        is_cursor = (abs_idx == node_cursor)
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

    return scroll, node_cursor


def draw_node_detail(win: Any, top: int, height: int, cols: int,
                     cache: Dict[str, Any], scroll: int,
                     detail_node_id: str,
                     node_sort: Tuple[int, bool]) -> None:
    """Draw detailed view for a single node."""
    nid = detail_node_id or "?"

    lines: List[Tuple[str, int]] = []

    # Title bar
    lines.append((f" NODE DETAIL: {nid}  [Esc/q]back  [r]refresh",
                   curses.A_BOLD | curses.A_UNDERLINE))
    lines.append(("", 0))

    # Find basic node info from nodes list
    node_rows = build_node_rows(cache, node_sort)
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
