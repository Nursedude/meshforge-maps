"""Events tab — live WebSocket event stream with pause/resume and type filtering."""

import curses
from typing import Any, Dict, List, Optional, Tuple

from ..helpers import (
    CP_ALERT_WARNING,
    CP_HIGHLIGHT,
    CP_SOURCE_ONLINE,
    _event_type_color,
    _format_ts,
    safe_addstr,
)


def draw_events(win: Any, top: int, height: int, cols: int,
                scroll: int, events: List[Dict[str, Any]],
                ws_connected: bool, events_paused: bool,
                event_type_filter: Optional[str],
                search_query: str) -> None:
    """Render the Events tab content.

    Parameters
    ----------
    events:
        Pre-resolved event list (either live log or paused snapshot).
    ws_connected:
        Whether the WebSocket is currently connected.
    events_paused:
        Whether the event stream display is paused.
    event_type_filter:
        Current type filter (None = show all).
    search_query:
        Active search/filter string.
    """
    lines: List[Tuple[str, int]] = []
    lines.append(("", 0))

    # Apply type filter
    if event_type_filter:
        events = [e for e in events
                  if event_type_filter in e.get("type", "")]

    # Apply search filter
    if search_query:
        q = search_query.lower()
        events = [e for e in events
                  if q in e.get("type", "").lower()
                  or q in e.get("node_id", "").lower()
                  or q in e.get("source", "").lower()
                  or q in str(e.get("data", "")).lower()]

    lines.append((" EVENT STREAM", curses.A_BOLD | curses.A_UNDERLINE))
    ws_status = "CONNECTED" if ws_connected else "POLLING"
    ws_attr = (curses.color_pair(CP_SOURCE_ONLINE) if ws_connected
               else curses.color_pair(CP_ALERT_WARNING))
    pause_str = "  PAUSED" if events_paused else ""
    filter_str = f"  Type:{event_type_filter}" if event_type_filter else ""
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
