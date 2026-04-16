"""System tab — dependency versions, Meshtastic API status, upgrade commands."""

import curses
from typing import Any, Dict, List, Tuple

from ..helpers import (
    CP_ALERT_WARNING,
    CP_SOURCE_OFFLINE,
    CP_SOURCE_ONLINE,
    safe_addstr,
)


def draw_system(win: Any, top: int, height: int, cols: int,
                cache: Dict[str, Any], scroll: int) -> None:
    """Render the System tab content showing dependency and upgrade info."""
    lines: List[Tuple[str, int]] = []
    lines.append(("", 0))

    deps = cache.get("dependencies") or {}
    packages = deps.get("packages", [])

    # Find meshtastic package entry
    mesh_pkg = None
    protobuf_pkg = None
    for pkg in packages:
        if pkg.get("name") == "meshtastic":
            mesh_pkg = pkg
        elif pkg.get("name") == "protobuf":
            protobuf_pkg = pkg

    # ── Meshtastic API Status ──
    lines.append((" MESHTASTIC API STATUS", curses.A_BOLD | curses.A_UNDERLINE))
    if mesh_pkg:
        installed = mesh_pkg.get("installed_version")
        latest = mesh_pkg.get("latest_version")
        upgrade = mesh_pkg.get("upgrade_available")

        if installed:
            lines.append((f"  Installed: meshtastic {installed}",
                          curses.color_pair(CP_SOURCE_ONLINE)))
        else:
            lines.append(("  Installed: meshtastic  (not installed)",
                          curses.color_pair(CP_SOURCE_OFFLINE)))

        if latest:
            if upgrade:
                lines.append((f"  Latest:    meshtastic {latest}  << upgrade available",
                              curses.color_pair(CP_ALERT_WARNING) | curses.A_BOLD))
            else:
                lines.append((f"  Latest:    meshtastic {latest}  (up to date)",
                              curses.color_pair(CP_SOURCE_ONLINE)))
        else:
            lines.append(("  Latest:    (could not check PyPI)", 0))
    else:
        lines.append(("  (dependency info unavailable)", 0))

    if protobuf_pkg:
        pb_ver = protobuf_pkg.get("installed_version", "not installed")
        lines.append((f"  Protobuf:  {pb_ver}", 0))

    lines.append(("", 0))

    # ── Upgrade Command ──
    upgrade_cmd = deps.get("upgrade_command")
    recommended = deps.get("recommended_version", ">=2.5.0")
    lines.append((" UPGRADE COMMAND", curses.A_BOLD | curses.A_UNDERLINE))
    if upgrade_cmd:
        lines.append(("  To upgrade, run:", 0))
        lines.append(("", 0))
        lines.append((f"    {upgrade_cmd}", curses.A_BOLD))
        lines.append(("", 0))
    else:
        lines.append(("  All packages up to date (or not installed).", 0))
    lines.append((f"  Recommended: meshtastic {recommended}", 0))
    lines.append(("", 0))

    # ── Optional Dependencies ──
    lines.append((" OPTIONAL DEPENDENCIES", curses.A_BOLD | curses.A_UNDERLINE))
    for pkg in packages:
        name = pkg.get("name", "?")
        installed = pkg.get("installed_version")
        if installed:
            indicator = "OK "
            ca = curses.color_pair(CP_SOURCE_ONLINE)
            ver_str = installed
        else:
            indicator = "-- "
            ca = curses.color_pair(CP_SOURCE_OFFLINE)
            ver_str = "not installed"
        lines.append((f"  [{indicator}] {name:<16} {ver_str}", ca))
    lines.append(("", 0))

    # ── Dependency Notes ──
    lines.append((" DEPENDENCY NOTES", curses.A_BOLD | curses.A_UNDERLINE))
    notes = [
        ("meshtastic", "Protobuf MQTT decoding & local meshtasticd daemon"),
        ("protobuf", "Required by meshtastic for message serialization"),
        ("paho-mqtt", "Live MQTT subscription to meshtastic broker"),
        ("websockets", "Real-time WebSocket push to map frontend"),
        ("pyOpenSSL", "TLS encryption for private MQTT brokers"),
        ("cryptography", "Cryptographic backend required by pyOpenSSL"),
    ]
    for name, note in notes:
        lines.append((f"  {name:<16} {note}", 0))
    lines.append(("", 0))

    lines.append((" MESHTASTIC API CHANGES (v2.5+)", curses.A_BOLD | curses.A_UNDERLINE))
    changes = [
        "MapReport (portnum 73) — self-reported node firmware/region/modem",
        "PowerMetrics — multi-channel voltage/current monitoring",
        "LocalStats — device packet counters, noise floor",
        "HostMetrics — Linux/Pi host system metrics",
        "EnvironmentMetrics — wind, rain, soil, UV/lux, radiation",
        "AirQualityMetrics — expanded PM/VOC/NOx/formaldehyde",
        "HealthMetrics — heart rate, SpO2, body temperature",
    ]
    for change in changes:
        lines.append((f"  + {change}", 0))

    # Render scrolled lines
    visible = lines[scroll:scroll + height]
    for i, (text, attr) in enumerate(visible):
        safe_addstr(win, top + i, 0, text, attr, cols)
