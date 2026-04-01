"""
MeshForge Maps - Interactive Setup Wizard

First-run configuration or reconfiguration via terminal prompts.
Writes settings to ~/.config/meshforge/plugins/org.meshforge.extension.maps/settings.json

Usage:
    python -m src.main --setup
"""

import json
import sys
from typing import Any, Dict, Optional

from ..utils.config import DEFAULT_CONFIG, MapsConfig


def _prompt(label: str, default: Any = None, password: bool = False) -> str:
    """Prompt user for input with a default value."""
    suffix = f" [{default}]" if default is not None else ""
    try:
        if password:
            import getpass
            value = getpass.getpass(f"  {label}{suffix}: ")
        else:
            value = input(f"  {label}{suffix}: ")
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return value.strip() if value.strip() else (str(default) if default is not None else "")


def _prompt_bool(label: str, default: bool = True) -> bool:
    """Prompt for a yes/no answer."""
    yn = "Y/n" if default else "y/N"
    try:
        value = input(f"  {label} [{yn}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    if not value:
        return default
    return value.startswith("y")


def _prompt_choice(label: str, options: list, default: str = "") -> str:
    """Prompt for a choice from a list."""
    print(f"  {label}:")
    for i, opt in enumerate(options, 1):
        marker = " *" if opt == default else ""
        print(f"    {i}. {opt}{marker}")
    try:
        value = input(f"  Choice [1-{len(options)}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    if not value:
        return default
    try:
        idx = int(value) - 1
        if 0 <= idx < len(options):
            return options[idx]
    except ValueError:
        pass
    return default


def run_setup() -> None:
    """Run the interactive setup wizard."""
    print()
    print("=" * 50)
    print("  MeshForge Maps - Setup Wizard")
    print("=" * 50)
    print()

    # Load existing config if present
    config = MapsConfig()
    settings: Dict[str, Any] = config.to_dict()

    # --- Network binding ---
    print("[Network]")
    host = _prompt("Bind address (0.0.0.0 for all interfaces)",
                   settings.get("http_host", "127.0.0.1"))
    port = _prompt("HTTP port", settings.get("http_port", 8808))
    try:
        port = int(port)
    except ValueError:
        port = 8808
    settings["http_host"] = host
    settings["http_port"] = port
    settings["ws_host"] = host
    print()

    # --- MQTT ---
    print("[MQTT Broker]")
    settings["mqtt_broker"] = _prompt("Broker address",
                                       settings.get("mqtt_broker", "mqtt.meshtastic.org"))
    mqtt_port = _prompt("Broker port", settings.get("mqtt_port", 1883))
    try:
        settings["mqtt_port"] = int(mqtt_port)
    except ValueError:
        settings["mqtt_port"] = 1883
    settings["mqtt_username"] = _prompt("Username", settings.get("mqtt_username", "meshdev"))
    pw = _prompt("Password (hidden)", default="", password=True)
    if pw:
        settings["mqtt_password"] = pw
    settings["mqtt_use_tls"] = _prompt_bool("Enable TLS?",
                                             settings.get("mqtt_use_tls", False))
    print()

    # --- MQTT Topic ---
    print("[MQTT Topic]")
    print("  Examples: msh/US, msh/US/HI, msh/US/Florida, msh/EU_868")
    settings["mqtt_topic"] = _prompt("Root topic (auto-expanded to /2/e/#)",
                                      settings.get("mqtt_topic", "msh/US"))
    print()

    # --- Data Sources ---
    print("[Data Sources]")
    settings["enable_meshtastic"] = _prompt_bool("Enable Meshtastic (MQTT)?",
                                                  settings.get("enable_meshtastic", True))
    settings["enable_reticulum"] = _prompt_bool("Enable Reticulum (RMAP.world)?",
                                                 settings.get("enable_reticulum", True))
    settings["enable_aredn"] = _prompt_bool("Enable AREDN (Worldmap)?",
                                             settings.get("enable_aredn", True))
    settings["enable_meshcore"] = _prompt_bool("Enable MeshCore (map.meshcore.dev)?",
                                                settings.get("enable_meshcore", True))
    settings["enable_hamclock"] = _prompt_bool("Enable HamClock / Space Weather?",
                                                settings.get("enable_hamclock", True))
    settings["enable_noaa_alerts"] = _prompt_bool("Enable NOAA Weather Alerts?",
                                                   settings.get("enable_noaa_alerts", True))
    print()

    # --- Map Display ---
    print("[Map Display]")
    lat = _prompt("Default center latitude", settings.get("map_center_lat", 20.0))
    lon = _prompt("Default center longitude", settings.get("map_center_lon", -100.0))
    zoom = _prompt("Default zoom level (1-18)", settings.get("map_default_zoom", 4))
    try:
        settings["map_center_lat"] = float(lat)
    except ValueError:
        pass
    try:
        settings["map_center_lon"] = float(lon)
    except ValueError:
        pass
    try:
        settings["map_default_zoom"] = int(zoom)
    except ValueError:
        pass
    print()

    # --- Security ---
    print("[Security]")
    print("  Set an API key to protect the admin settings UI.")
    print("  Leave blank for no authentication (local/trusted networks).")
    api_key = _prompt("Admin API key (hidden)", default="", password=True)
    if api_key:
        settings["api_key"] = api_key
    else:
        settings["api_key"] = None
    print()

    # --- Meshtastic source mode ---
    print("[Meshtastic Source Mode]")
    mode = _prompt_choice(
        "How should Meshtastic data be collected?",
        ["auto", "mqtt_only", "local_only"],
        settings.get("meshtastic_source", "auto"),
    )
    settings["meshtastic_source"] = mode
    print()

    # --- Save ---
    config.update(settings)
    config.save()

    print("=" * 50)
    print("  Configuration saved!")
    print(f"  File: {config._config_path}")
    print()
    print("  Start the server:")
    print("    sudo systemctl start meshforge-maps")
    print("  Or run directly:")
    print("    python -m src.main --host 0.0.0.0")
    print()
    if settings.get("api_key"):
        print("  Admin API key is set. Use it in the web UI")
        print("  to access Settings, or pass via header:")
        print("    X-MeshForge-Key: <your-key>")
        print()
    print("  Re-run this wizard anytime:")
    print("    python -m src.main --setup")
    print("=" * 50)
    print()
