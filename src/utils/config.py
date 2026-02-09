"""
MeshForge Maps - Configuration Management

Handles loading, saving, and validating extension settings.
Settings persist to ~/.config/meshforge/plugins/org.meshforge.extension.maps/settings.json
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: Dict[str, Any] = {
    "default_tile_provider": "carto_dark",
    "enable_meshtastic": True,
    "enable_reticulum": True,
    "enable_hamclock": True,
    "enable_aredn": True,
    "hamclock_host": "localhost",
    "hamclock_port": 8080,
    "openhamclock_port": 3000,
    "map_center_lat": 20.0,
    "map_center_lon": -100.0,
    "map_default_zoom": 4,
    "cache_ttl_minutes": 15,
    "http_port": 8808,
    # MQTT broker configuration (upstream: private broker support)
    "mqtt_broker": "mqtt.meshtastic.org",
    "mqtt_port": 1883,
    "mqtt_topic": "msh/#",
    "mqtt_username": None,
    "mqtt_password": None,
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
}


class MapsConfig:
    """Configuration manager for MeshForge Maps extension."""

    def __init__(self, config_path: Optional[Path] = None):
        if config_path:
            self._config_path = config_path
        else:
            self._config_path = (
                Path.home()
                / ".config"
                / "meshforge"
                / "plugins"
                / "org.meshforge.extension.maps"
                / "settings.json"
            )
        self._settings: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        """Load settings from disk, falling back to defaults."""
        if self._config_path.exists():
            try:
                with open(self._config_path, "r") as f:
                    saved = json.load(f)
                for key, value in saved.items():
                    if key in DEFAULT_CONFIG:
                        self._settings[key] = value
                logger.info("Loaded settings from %s", self._config_path)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load settings: %s, using defaults", e)
        else:
            logger.info("No settings file found, using defaults")

    def save(self) -> None:
        """Persist current settings to disk."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._config_path, "w") as f:
                json.dump(self._settings, f, indent=2)
            logger.info("Saved settings to %s", self._config_path)
        except OSError as e:
            logger.error("Failed to save settings: %s", e)

    def get(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if key in DEFAULT_CONFIG:
            self._settings[key] = value

    def update(self, settings: Dict[str, Any]) -> None:
        for key, value in settings.items():
            self.set(key, value)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._settings)

    def get_tile_providers(self) -> Dict[str, Dict[str, str]]:
        return dict(TILE_PROVIDERS)

    def get_enabled_sources(self) -> list:
        sources = []
        if self._settings.get("enable_meshtastic"):
            sources.append("meshtastic")
        if self._settings.get("enable_reticulum"):
            sources.append("reticulum")
        if self._settings.get("enable_hamclock"):
            sources.append("hamclock")
        if self._settings.get("enable_aredn"):
            sources.append("aredn")
        return sources
