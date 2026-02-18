"""Propagation tab — HF band conditions, space weather, DX spots."""

import curses
from typing import Any, Dict, List, Tuple

from ..helpers import (
    CP_ALERT_CRITICAL,
    CP_ALERT_WARNING,
    CP_HEALTH_EXCELLENT,
    CP_HEALTH_FAIR,
    CP_HEALTH_GOOD,
    CP_HEALTH_POOR,
    CP_HIGHLIGHT,
    CP_SOURCE_ONLINE,
    safe_addstr,
)


def draw_propagation(win: Any, top: int, height: int, cols: int,
                     cache: Dict[str, Any], scroll: int) -> None:
    """Render the Propagation tab content."""
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
