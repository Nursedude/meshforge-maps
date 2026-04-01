"""
MeshForge Maps - Configuration Management

Handles loading, saving, and validating extension settings.
Settings persist to ~/.config/meshforge/plugins/org.meshforge.extension.maps/settings.json
"""

import json
import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .paths import get_real_home

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: Dict[str, Any] = {
    "default_tile_provider": "carto_dark",
    "enable_meshtastic": True,
    "enable_reticulum": True,
    "enable_hamclock": True,
    "enable_aredn": True,
    "enable_noaa_alerts": True,
    # Meshtastic data source mode: "auto" (API → MQTT → cache), "mqtt_only", "local_only"
    "meshtastic_source": "auto",
    # NOAA weather alerts (api.weather.gov)
    "noaa_alerts_area": None,  # State code filter (e.g. "TX", "CA"); None = all US
    "noaa_alerts_severity": None,  # Severity filter list (e.g. ["Extreme","Severe"]); None = all
    "hamclock_host": "localhost",
    "hamclock_port": 8080,
    "openhamclock_port": 3000,
    "map_center_lat": 20.0,
    "map_center_lon": -100.0,
    "map_default_zoom": 4,
    "cache_ttl_minutes": 15,
    "http_port": 8808,
    "http_host": "127.0.0.1",
    "ws_host": "127.0.0.1",
    # MQTT broker configuration
    # Public broker defaults: meshdev/large4cats on port 1883 (plaintext)
    # Set mqtt_use_tls=True and mqtt_port=8883 for encrypted connections
    "mqtt_broker": "mqtt.meshtastic.org",
    "mqtt_port": 1883,
    "mqtt_topic": "msh/US/2/e/#",
    "mqtt_username": "meshdev",
    "mqtt_password": "large4cats",
    "mqtt_use_tls": False,
    # CORS: None = same-origin (no CORS headers sent); set to "*" or a specific origin to enable
    "cors_allowed_origin": None,
    # API key for protecting /api/ endpoints (None = no auth required)
    "api_key": None,
    # Meshtastic API proxy port (meshtasticd-compatible JSON proxy)
    "meshtastic_proxy_port": 4404,
    # meshtasticd HTTP API connection
    "meshtasticd_host": "localhost",
    "meshtasticd_port": 4403,
    # AREDN auto-discovery targets (queried on port 8080 for sysinfo.json)
    "aredn_node_targets": ["localnode.local.mesh", "10.0.0.1", "localnode"],
    # Reticulum Community Hub (RCH) API
    "rch_host": "localhost",
    "rch_port": 8000,
    "rch_api_key": None,
    # Public map data sources
    "enable_rmap_public": True,       # Fetch RMAP.world Reticulum node data
    "enable_aredn_worldmap": True,    # Fetch AREDN worldmap node data
}

# Tile provider definitions for Leaflet.js
TILE_PROVIDERS: Dict[str, Dict[str, str]] = {
    "carto_dark": {
        "name": "CartoDB Dark Matter",
        "url": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        "attribution": '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
        "max_zoom": "20",
    },
    "osm_standard": {
        "name": "OpenStreetMap",
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        "max_zoom": "19",
    },
    "osm_topo": {
        "name": "OpenTopoMap",
        "url": "https://tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": '&copy; <a href="https://opentopomap.org">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)',
        "max_zoom": "17",
    },
    "esri_satellite": {
        "name": "Esri Satellite",
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "&copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics",
        "max_zoom": "19",
    },
    "esri_topo": {
        "name": "Esri Topographic",
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        "attribution": "&copy; Esri &mdash; Sources: Esri, HERE, Garmin, USGS, NGA",
        "max_zoom": "19",
    },
    "stadia_terrain": {
        "name": "Stadia Stamen Terrain",
        "url": "https://tiles.stadiamaps.com/tiles/stamen_terrain/{z}/{x}/{y}.png",
        "attribution": '&copy; <a href="https://stadiamaps.com/">Stadia Maps</a> &copy; <a href="https://stamen.com">Stamen Design</a>',
        "max_zoom": "18",
    },
}

# Network color scheme (matches meshforge core)
NETWORK_COLORS: Dict[str, str] = {
    "meshtastic": "#66bb6a",
    "reticulum": "#ab47bc",
    "aredn": "#ff7043",
    "hamclock": "#42a5f5",
    "noaa_alerts": "#f44336",
}


class MapsConfig:
    """Configuration manager for MeshForge Maps extension."""

    def __init__(self, config_path: Optional[Path] = None):
        if config_path:
            self._config_path = config_path
        else:
            self._config_path = (
                get_real_home()
                / ".config"
                / "meshforge"
                / "plugins"
                / "org.meshforge.extension.maps"
                / "settings.json"
            )
        self._lock = threading.Lock()
        self._settings: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        """Load settings from disk, falling back to defaults."""
        if self._config_path.exists():
            try:
                with open(self._config_path, "r") as f:
                    saved = json.load(f)
                with self._lock:
                    for key, value in saved.items():
                        if key not in DEFAULT_CONFIG:
                            continue
                        # Don't let saved None values suppress upgraded non-None defaults
                        # (e.g. old settings.json with "mqtt_username": null)
                        if value is None and DEFAULT_CONFIG[key] is not None:
                            continue
                        self._settings[key] = value
                logger.info("Loaded settings from %s", self._config_path)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load settings: %s, using defaults", e)
        else:
            logger.info("No settings file found, using defaults")

    def save(self) -> None:
        """Persist current settings to disk with atomic write."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._lock:
                snapshot = dict(self._settings)

            # Create single-generation backup
            if self._config_path.exists():
                backup_path = self._config_path.with_suffix(".json.bak")
                try:
                    shutil.copy2(str(self._config_path), str(backup_path))
                except OSError as e:
                    logger.warning("Failed to create config backup: %s", e)

            # Atomic write: temp file + os.replace()
            old_umask = os.umask(0o077)
            try:
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(self._config_path.parent),
                    prefix=".settings_",
                    suffix=".tmp",
                )
                try:
                    with os.fdopen(fd, "w") as f:
                        json.dump(snapshot, f, indent=2)
                    os.replace(tmp_path, str(self._config_path))
                except Exception:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
            finally:
                os.umask(old_umask)
            logger.info("Saved settings to %s", self._config_path)
        except OSError as e:
            logger.error("Failed to save settings: %s", e)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key in DEFAULT_CONFIG:
                self._settings[key] = value

    def update(self, settings: Dict[str, Any]) -> None:
        with self._lock:
            for key, value in settings.items():
                if key in DEFAULT_CONFIG:
                    self._settings[key] = value

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._settings)

    @staticmethod
    def validate_update(data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        """Validate a config update payload. Returns (validated, errors)."""
        validated: Dict[str, Any] = {}
        errors: List[str] = []
        for key, value in data.items():
            if key not in DEFAULT_CONFIG:
                errors.append(f"Unknown config key: {key}")
                continue
            # Type-specific validation
            if key == "mqtt_port":
                try:
                    port = int(value)
                except (ValueError, TypeError):
                    errors.append("mqtt_port must be an integer")
                    continue
                if not (1 <= port <= 65535):
                    errors.append("mqtt_port must be between 1 and 65535")
                    continue
                validated[key] = port
            elif key == "mqtt_broker":
                if not isinstance(value, str) or not value.strip():
                    errors.append("mqtt_broker must be a non-empty string")
                    continue
                validated[key] = value.strip()
            elif key == "mqtt_topic":
                if not isinstance(value, str) or not value.strip():
                    errors.append("mqtt_topic must be a non-empty string")
                    continue
                validated[key] = value.strip()
            elif key == "mqtt_use_tls":
                if not isinstance(value, bool):
                    errors.append("mqtt_use_tls must be a boolean")
                    continue
                validated[key] = value
            elif key in ("mqtt_username", "mqtt_password"):
                # Allow string or None
                if value is not None and not isinstance(value, str):
                    errors.append(f"{key} must be a string or null")
                    continue
                validated[key] = value if value else None
            else:
                validated[key] = value
        return validated, errors

    def get_tile_providers(self) -> Dict[str, Dict[str, str]]:
        return dict(TILE_PROVIDERS)

    def get_enabled_sources(self) -> list:
        with self._lock:
            settings = dict(self._settings)
        sources = []
        if settings.get("enable_meshtastic"):
            sources.append("meshtastic")
        if settings.get("enable_reticulum"):
            sources.append("reticulum")
        if settings.get("enable_hamclock"):
            sources.append("hamclock")
        if settings.get("enable_aredn"):
            sources.append("aredn")
        if settings.get("enable_noaa_alerts"):
            sources.append("noaa_alerts")
        return sources
