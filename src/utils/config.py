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
    # Deployment profile: "full" (all sources, default) or "lite" (Pi 2W / low-power)
    # Lite mode disables heavy collectors and increases cache TTL
    "deployment_profile": "full",
    "default_tile_provider": "carto_dark",
    "enable_meshtastic": True,
    "enable_reticulum": True,
    "enable_hamclock": True,
    "enable_aredn": True,
    "enable_meshcore": True,
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
    "node_history_throttle_seconds": 300,   # 5 min — min gap between observations per node
    # Stationary-node heartbeat. When (lat, lon, network) match the last
    # record, skip the insert until this interval has elapsed. Mobile nodes
    # still write on every position change. Set to 0 to disable value-dedup
    # (legacy time-only throttle).
    "node_history_heartbeat_seconds": 3600,  # 1 h
    "node_history_retention_days": 3,       # keep last 3 days of observations
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
    # Optional: additional MQTT brokers that feed the same node store in parallel.
    # Each entry: {"broker": host, "port": int, "topic": str, "username": str,
    # "password": str, "use_tls": bool, "label": str}. If empty, only the scalar
    # mqtt_* keys above are used.
    "mqtt_brokers": [],
    # CORS: None = same-origin (no CORS headers sent); set to "*" or a specific origin to enable
    "cors_allowed_origin": None,
    # API key for protecting /api/ endpoints (None = no auth required)
    "api_key": None,
    # Meshtastic API proxy port (meshtasticd-compatible JSON proxy)
    "meshtastic_proxy_port": 4404,
    # meshtasticd HTTP API connection
    "meshtasticd_host": "localhost",
    "meshtasticd_port": 4403,
    # AREDN auto-discovery IPs (queried on port 8080 for sysinfo.json).
    # Canonical key — matches `aredn_node_ips` in meshforge core's
    # ~/.config/meshforge/map_settings.json so operators see the same
    # name in both `:5000` and `:8808` configs.
    "aredn_node_ips": ["localnode.local.mesh", "10.0.0.1", "localnode"],
    # DEPRECATED — legacy key kept only for one-cycle compat. Operators
    # with this set in their saved settings.json will see a one-shot
    # warning at MapsConfig.load(); the value is copied into
    # aredn_node_ips for the running process. Remove after fleet rename
    # (planned post-Phase B audit).
    "aredn_node_targets": None,
    # Reticulum Community Hub (RCH) API
    "rch_host": "localhost",
    "rch_port": 8000,
    "rch_api_key": None,
    # Public map data sources
    "enable_rmap_public": True,       # Fetch RMAP.world Reticulum node data
    "enable_aredn_worldmap": True,    # Fetch AREDN worldmap node data
    "enable_meshcore_map": True,      # Fetch MeshCore map node data
    # Region preset: bundles map center, zoom, and MQTT topic
    "region_preset": None,            # None = first-run (show picker), or key from REGION_PRESETS
    # Optional subsystems (disable to reduce memory on constrained devices)
    "enable_config_drift": True,      # Config drift detection (tracks firmware/hardware changes)
    "enable_node_state": True,        # Node state machine (online/offline/intermittent tracking)
    "enable_analytics": True,         # Historical analytics (growth, heatmap, ranking)
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
    "meshcore": "#26c6da",
    "hamclock": "#42a5f5",
    "noaa_alerts": "#f44336",
}

# Simplified CONUS land boundary for point-in-polygon region scoping.
# ~55 vertices, [lon, lat] pairs, clockwise from the NW corner. Target precision:
# excludes Toronto/Montreal/Windsor/Tijuana/Juárez/Bahamas/Havana while keeping
# Seattle/Key West/Brownsville/El Paso/San Diego/Bangor inside. Border-town
# accuracy is not guaranteed; this is a filter, not a survey.
US_CONUS_POLYGON: List[List[float]] = [
    [-123.00, 49.00],   # Point Roberts / NW corner (US-BC)
    # US-Canada 49°N parallel east to MN
    [-95.15, 49.00],
    # MN northern jag / Lake of the Woods
    [-94.82, 48.70],
    [-93.84, 48.63],
    [-91.60, 48.10],
    # Lake Superior (border through lake — hug south shore)
    [-88.00, 47.40],
    # Upper Peninsula MI
    [-84.88, 46.50],    # Sault Ste Marie area
    [-84.12, 45.95],    # Mackinac
    [-83.40, 45.00],
    # Lower MI / Lake Huron (ON-MI border through lake)
    [-82.50, 44.00],
    [-82.50, 43.10],
    # Detroit — keep Windsor (ON, lat 42.30) out by hugging the river west side
    [-83.15, 42.15],
    [-82.80, 41.50],    # Lake Erie south shore
    # Lake Erie south shore, Niagara, Lake Ontario south shore
    [-79.05, 42.25],    # PA/NY Erie
    [-79.05, 43.27],    # Niagara — Toronto is 43.7 (outside)
    [-76.60, 43.55],    # Oswego
    [-75.30, 43.90],
    [-74.60, 44.55],
    [-73.30, 44.95],    # VT/NH/QC
    [-71.00, 45.00],
    [-70.00, 45.70],    # ME
    [-69.20, 47.46],    # ME north corner
    [-68.00, 47.35],
    [-67.78, 45.70],    # ME east (St. Croix River)
    [-66.98, 44.80],    # Eastport/Calais
    # Atlantic coast south from Maine
    [-67.80, 44.50],
    [-69.00, 43.80],    # Portland ME
    [-70.50, 42.80],    # MA N
    [-70.00, 41.50],    # Cape Cod
    [-72.00, 41.15],
    [-73.70, 40.55],    # Long Island S shore
    [-74.20, 39.30],    # NJ
    [-75.10, 38.40],
    [-76.00, 37.00],
    [-75.50, 35.20],    # NC Outer Banks
    [-77.95, 33.80],
    [-80.80, 32.00],    # SC/GA
    [-81.30, 30.50],    # FL N Atlantic
    [-79.95, 26.80],    # FL E (Miami coast ~-80.13)
    [-80.30, 25.20],    # FL S
    [-81.80, 24.50],    # Key West
    # Gulf of Mexico FL -> TX
    [-82.70, 25.80],
    [-83.40, 27.80],
    [-84.90, 29.80],
    [-87.50, 30.30],
    [-88.70, 30.20],
    [-89.40, 29.00],    # LA delta
    [-93.80, 29.50],
    [-97.00, 27.80],
    [-97.20, 25.83],    # Rio Grande mouth (south of Brownsville 25.90)
    # Rio Grande border NW
    [-97.80, 25.95],
    [-99.10, 26.50],
    [-100.40, 28.45],
    [-101.40, 29.80],
    [-102.80, 29.80],   # Big Bend
    [-104.50, 30.60],
    [-105.50, 31.00],
    [-106.50, 31.80],   # El Paso area (Juárez 31.70 outside)
    # NM/AZ south border (approx 31°20'N)
    [-108.20, 31.33],
    [-111.05, 31.33],
    [-114.81, 32.55],   # AZ/CA corner near Yuma (north of Mexicali 32.65? actually Mexicali 32.65, keep polygon north)
    # CA-Mex border (real border ~32.535°N; place at 32.55 to keep Tijuana 32.53 out)
    [-117.13, 32.55],
    # Pacific coast north to NW corner
    [-117.25, 33.00],
    [-118.50, 34.00],
    [-120.70, 34.50],
    [-121.95, 36.60],
    [-122.50, 37.80],   # SF
    [-123.80, 39.00],
    [-124.40, 40.00],
    [-124.40, 42.00],
    [-124.30, 43.60],
    [-124.10, 46.20],
    [-124.80, 48.40],
    [-123.00, 49.00],   # close
]


# Region presets — bundles map center, zoom, and MQTT topic into one-click templates
REGION_PRESETS: Dict[str, Dict[str, Any]] = {
    "hawaii": {
        "label": "Hawaii",
        "map_center_lat": 20.5,
        "map_center_lon": -157.0,
        "map_default_zoom": 7,
        "mqtt_topic": "msh/US/HI",
        "bbox": [18.5, -161.0, 22.5, -154.0],  # [south, west, north, east]
    },
    "west_coast": {
        "label": "West Coast",
        "map_center_lat": 37.5,
        "map_center_lon": -122.0,
        "map_default_zoom": 6,
        "mqtt_topic": "msh/US",
        "bbox": [32.0, -125.0, 49.0, -114.0],
    },
    "us": {
        "label": "United States",
        "map_center_lat": 39.0,
        "map_center_lon": -98.0,
        "map_default_zoom": 4,
        "mqtt_topic": "msh/US",
        # Islands use bboxes (no shared borders); CONUS uses a land polygon
        # to avoid rectangular leaks into Mexico/Canada/Bahamas.
        "bbox": [
            [51.0, -180.0, 72.0, -130.0],  # Alaska
            [18.5, -161.0, 22.5, -154.0],  # Hawaii
            [17.5, -68.0, 18.6, -64.0],    # PR + USVI
        ],
        "polygons": [US_CONUS_POLYGON],
    },
    "americas": {
        "label": "Americas",
        "map_center_lat": 15.0,
        "map_center_lon": -80.0,
        "map_default_zoom": 3,
        "mqtt_topic": "msh/#",
        "bbox": [-56.0, -180.0, 72.0, -34.0],  # Tierra del Fuego to Alaska
    },
    "world": {
        "label": "World",
        "map_center_lat": 20.0,
        "map_center_lon": 0.0,
        "map_default_zoom": 3,
        "mqtt_topic": "msh/#",
        "bbox": None,
    },
}


# Deployment-profile overrides applied by MapsConfig.get_effective().
# Each entry maps a config key to a lambda that transforms the raw value.
# A missing profile (e.g. "full") receives no overrides.
_LITE_OVERRIDES: Dict[str, Any] = {
    "cache_ttl_minutes": lambda v: max(v if isinstance(v, (int, float)) else 60, 60),
    "node_history_throttle_seconds": lambda v: max(v if isinstance(v, (int, float)) else 600, 600),
    # Lite Pis don't need hourly heartbeats. 6 h floor: a stationary repeater
    # writes 4 rows/day instead of the default 24.
    "node_history_heartbeat_seconds": lambda v: max(v if isinstance(v, (int, float)) else 21600, 21600),
    "node_history_retention_days": lambda v: min(v if isinstance(v, (int, float)) else 1, 1),
    "enable_config_drift": lambda v: False,
    "enable_node_state": lambda v: False,
    "enable_analytics": lambda v: False,
}

_MEDIUM_OVERRIDES: Dict[str, Any] = {
    "cache_ttl_minutes": lambda v: max(v if isinstance(v, (int, float)) else 30, 30),
    "node_history_throttle_seconds": lambda v: max(v if isinstance(v, (int, float)) else 300, 300),
    "node_history_heartbeat_seconds": lambda v: max(v if isinstance(v, (int, float)) else 3600, 3600),
    "node_history_retention_days": lambda v: min(v if isinstance(v, (int, float)) else 2, 2),
    # analytics/state/drift stay ON in medium — that's the point of the tier
}

_PROFILE_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "lite": _LITE_OVERRIDES,
    "medium": _MEDIUM_OVERRIDES,
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
                saved_keys: set = set()
                with self._lock:
                    for key, value in saved.items():
                        if key not in DEFAULT_CONFIG:
                            continue
                        # Don't let saved None values suppress upgraded non-None defaults
                        # (e.g. old settings.json with "mqtt_username": null)
                        if value is None and DEFAULT_CONFIG[key] is not None:
                            continue
                        self._settings[key] = value
                        saved_keys.add(key)
                    self._migrate_legacy_aredn(saved_keys)
                logger.info("Loaded settings from %s", self._config_path)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load settings: %s, using defaults", e)
        else:
            logger.info("No settings file found, using defaults")

    def _migrate_legacy_aredn(self, saved_keys: set) -> None:
        """One-cycle compat: legacy aredn_node_targets → aredn_node_ips.

        If the saved config sets only the deprecated key, copy its value
        into aredn_node_ips for the running process and emit a one-shot
        warning so operators know to rename. Caller holds self._lock.
        """
        legacy_set = "aredn_node_targets" in saved_keys
        canonical_set = "aredn_node_ips" in saved_keys
        if legacy_set and not canonical_set:
            legacy_value = self._settings.get("aredn_node_targets")
            if legacy_value:
                self._settings["aredn_node_ips"] = legacy_value
            logger.warning(
                "Config %s uses deprecated key 'aredn_node_targets'; "
                "rename to 'aredn_node_ips' (matches meshforge core). "
                "Legacy key will stop being read in a future release. "
                "Run /opt/meshforge/scripts/aredn_config_audit.sh for a "
                "fleet-wide check.",
                self._config_path,
            )

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
            elif key == "region_preset":
                if value is not None and value not in REGION_PRESETS and value != "custom":
                    errors.append(f"region_preset must be one of: {', '.join(REGION_PRESETS)}, custom, or null")
                    continue
                validated[key] = value
            else:
                validated[key] = value
        return validated, errors

    @property
    def is_lite(self) -> bool:
        """True if running in lite deployment profile (Pi 2W / low-power)."""
        return self.get("deployment_profile") == "lite"

    @property
    def is_medium(self) -> bool:
        """True if running in medium deployment profile (Pi 4/5, 4-8GB)."""
        return self.get("deployment_profile") == "medium"

    def get_effective(self, key: str, default: Any = None) -> Any:
        """Get config value with deployment-profile tuning applied.

        Lite: resource-constrained (Pi 2W). Disables analytics/state/drift and
        enforces long cache/history intervals.
        Medium: middling hardware (Pi 4/5, 4-8GB). Keeps analytics/state/drift
        enabled but smooths I/O.
        Full: no overrides.
        """
        value = self.get(key, default)
        overrides = _PROFILE_OVERRIDES.get(self.get("deployment_profile"))
        if overrides is None:
            return value
        override = overrides.get(key)
        if override:
            return override(value)
        return value

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
        if settings.get("enable_meshcore"):
            sources.append("meshcore")
        if settings.get("enable_noaa_alerts"):
            sources.append("noaa_alerts")
        return sources
