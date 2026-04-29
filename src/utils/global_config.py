"""
MeshForge ecosystem-wide shared identity / config layer (read side).

Reads ``~/.config/meshforge/global.ini`` — the canonical source of truth
for values that span multiple MeshForge apps (NOC, maps, meshing_around,
MeshAnchor). Maps consumes it as a *fallback* before its own per-plugin
``settings.json`` loads, so per-plugin values still take precedence.

Contract spec lives in the meshing_around_meshforge repo at
``docs/global_config.md`` — that's the canonical schema. This module
mirrors its INI reader but emits a flat dict keyed to maps'
``DEFAULT_CONFIG`` shape (e.g. global ``[mqtt] broker`` → ``mqtt_broker``).

Layering: dataclass / DEFAULT_CONFIG defaults < global.ini < per-app
settings.json < runtime overrides.

Missing file → no-op, current behavior preserved. Malformed INI → log
DEBUG and bail; never raise.
"""

import configparser
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .paths import get_real_home

logger = logging.getLogger(__name__)

GLOBAL_CONFIG_FILENAME = "global.ini"
GLOBAL_CONFIG_DIRNAME = "meshforge"


def global_config_path() -> Path:
    """Canonical path: ``~/.config/meshforge/global.ini``.

    Uses maps' :func:`utils.paths.get_real_home` so sudo / systemd never
    redirect to /root.
    """
    return get_real_home() / ".config" / GLOBAL_CONFIG_DIRNAME / GLOBAL_CONFIG_FILENAME


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> Optional[bool]:
    """Return None on missing/blank; bool otherwise.

    None lets the seeding logic distinguish "global said nothing" from
    "global said False" — important because mqtt_use_tls=False is a real
    setting we don't want to skip.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s == "":
        return None
    return s in ("true", "yes", "1", "on")


def load_global_overrides(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read global.ini and return a flat dict of maps-config overrides.

    Keys in the returned dict match maps' :data:`DEFAULT_CONFIG` shape so
    callers can directly merge into ``MapsConfig._settings`` before
    ``settings.json`` loads.

    Missing or malformed file → empty dict, never raises.
    """
    target = Path(path) if path else global_config_path()
    overrides: Dict[str, Any] = {}

    if not target.exists():
        return overrides

    parser = configparser.ConfigParser()
    try:
        parser.read(str(target))
    except (configparser.Error, OSError, UnicodeDecodeError) as e:
        # Malformed file shouldn't crash the maps server — every app on
        # the Pi would die at boot if global.ini got corrupted.
        logger.debug("MeshForge global.ini parse failed (%s): %s", type(e).__name__, e)
        return overrides

    # ---- [mqtt] → mqtt_* keys -----------------------------------------
    if parser.has_section("mqtt"):
        broker = parser.get("mqtt", "broker", fallback="").strip()
        if broker:
            overrides["mqtt_broker"] = broker

        port = _coerce_int(parser.get("mqtt", "port", fallback=""), 0)
        if port:
            overrides["mqtt_port"] = port

        use_tls = _coerce_bool(parser.get("mqtt", "use_tls", fallback=None))
        if use_tls is not None:
            overrides["mqtt_use_tls"] = use_tls

        username = parser.get("mqtt", "username", fallback="").strip()
        if username:
            overrides["mqtt_username"] = username

        password = parser.get("mqtt", "password", fallback="")
        # Don't strip password — leading/trailing whitespace may be intentional
        if password:
            overrides["mqtt_password"] = password

        # global's `topic_root` (e.g. "msh/US") is a prefix; maps wants
        # the full subscribe pattern. Build the standard v2 wildcard if
        # the operator hasn't specified the trailing wildcard themselves.
        topic_root = parser.get("mqtt", "topic_root", fallback="").strip()
        if topic_root:
            if topic_root.endswith("/#") or "/2/" in topic_root:
                overrides["mqtt_topic"] = topic_root
            else:
                overrides["mqtt_topic"] = f"{topic_root.rstrip('/')}/2/e/#"

    # ---- [region] → region_preset + map_center_* ----------------------
    if parser.has_section("region"):
        preset = parser.get("region", "preset", fallback="").strip()
        if preset:
            # Map global preset names (matching meshing_around_meshforge
            # profile names) to maps REGION_PRESETS keys when they differ.
            preset_map = {
                "default_us": "us",
                "us_default": "us",
                "europe": None,        # maps has no europe preset yet
                "australia_nz": None,  # maps has no anz preset yet
                "anz": None,
            }
            mapped = preset_map.get(preset, preset)
            if mapped:
                overrides["region_preset"] = mapped

        home_lat = _coerce_float(parser.get("region", "home_lat", fallback=None))
        home_lon = _coerce_float(parser.get("region", "home_lon", fallback=None))
        if home_lat is not None and -90.0 <= home_lat <= 90.0:
            overrides["map_center_lat"] = home_lat
        if home_lon is not None and -180.0 <= home_lon <= 180.0:
            overrides["map_center_lon"] = home_lon

    if overrides:
        logger.info(
            "MeshForge global config applied %d override(s) from %s",
            len(overrides), target,
        )

    return overrides
