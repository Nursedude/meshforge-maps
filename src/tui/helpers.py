"""Shared helpers for the MeshForge Maps TUI.

Color constants, color-mapping functions, safe drawing utilities, and
timestamp formatting used across all tab modules.
"""

import curses
import time
from typing import Any


# ── Color pair IDs ────────────────────────────────────────────────

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


def _format_ts(ts: float) -> str:
    """Format a unix timestamp as HH:MM:SS."""
    if not ts:
        return "--:--:--"
    try:
        return time.strftime("%H:%M:%S", time.localtime(ts))
    except (OSError, ValueError):
        return "??:??:??"


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
