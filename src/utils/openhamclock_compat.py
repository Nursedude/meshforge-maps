"""
MeshForge Maps - OpenHamClock API Compatibility Layer

Maps OpenHamClock REST API responses to the format expected by HamClockCollector.

Original HamClock (WB0OEW, SK) ceases operation June 2026.
OpenHamClock (MIT, https://github.com/accius/openhamclock) is the community
successor, running on port 3000 by default.

Known API differences between HamClock and OpenHamClock:
  - OpenHamClock may use lowercase response keys
  - OpenHamClock get_voacap.txt may include additional fields
  - OpenHamClock get_sys.txt includes "Version=OpenHamClock x.y.z"
  - OpenHamClock get_dxspots.txt format may differ in spot ordering
  - OpenHamClock may support additional endpoints (get_config.txt)

This module normalizes responses from either variant into a consistent format.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# Key normalization mappings: OpenHamClock lowercase -> canonical HamClock keys
# Both variants should work, but OpenHamClock sometimes uses different key names
_SPACEWX_KEY_ALIASES: Dict[str, str] = {
    # OpenHamClock may use these lowercase variants
    "sfi": "SFI",
    "flux": "SFI",
    "solar_flux": "SFI",
    "kp": "Kp",
    "kp_index": "Kp",
    "a": "A",
    "a_index": "A",
    "xray": "Xray",
    "x-ray": "Xray",
    "xray_flux": "Xray",
    "ssn": "SSN",
    "sunspot": "SSN",
    "sunspots": "SSN",
    "proton": "Proton",
    "pf": "Proton",
    "proton_flux": "Proton",
    "aurora": "Aurora",
    "aur": "Aurora",
}

_DE_DX_KEY_ALIASES: Dict[str, str] = {
    "latitude": "lat",
    "longitude": "lng",
    "lon": "lng",
    "callsign": "call",
    "gridsquare": "grid",
    "grid_square": "grid",
}

_BAND_KEY_ALIASES: Dict[str, str] = {
    # Map individual OpenHamClock band keys to distinct canonical names.
    # Previous mapping collapsed two bands into one key (e.g. band80m and
    # band40m both -> "80m-40m"), silently losing whichever was processed
    # second.  Each band now maps to its own canonical key.
    "band80m": "80m",
    "band40m": "40m",
    "band30m": "30m",
    "band20m": "20m",
    "band17m": "17m",
    "band15m": "15m",
    "band12m": "12m",
    "band10m": "10m",
}


def normalize_key_value(parsed: Dict[str, str], alias_map: Dict[str, str]) -> Dict[str, str]:
    """Normalize keys in a parsed key=value dict using alias mappings.

    Case-insensitive key matching. Returns a new dict with canonical keys.
    Original keys that don't match any alias are preserved as-is.
    """
    result: Dict[str, str] = {}
    for key, value in parsed.items():
        canonical = alias_map.get(key.lower().strip())
        if canonical:
            result[canonical] = value
        else:
            result[key] = value
    return result


def normalize_spacewx(parsed: Dict[str, str]) -> Dict[str, str]:
    """Normalize space weather response keys from either HamClock variant."""
    return normalize_key_value(parsed, _SPACEWX_KEY_ALIASES)


def normalize_de_dx(parsed: Dict[str, str]) -> Dict[str, str]:
    """Normalize DE/DX location response keys from either HamClock variant."""
    return normalize_key_value(parsed, _DE_DX_KEY_ALIASES)


def normalize_band_conditions(parsed: Dict[str, str]) -> Dict[str, str]:
    """Normalize band condition response keys from either HamClock variant."""
    return normalize_key_value(parsed, _BAND_KEY_ALIASES)


def detect_variant(sys_text: str) -> str:
    """Detect whether the responding server is HamClock or OpenHamClock.

    Parses the get_sys.txt response text for version identification.

    Returns:
        "openhamclock" if Version contains "OpenHamClock" or "openhamclock"
        "hamclock" otherwise
    """
    if not sys_text:
        return "hamclock"
    lower = sys_text.lower()
    if "openhamclock" in lower:
        return "openhamclock"
    return "hamclock"


def get_endpoint_map(variant: str) -> Dict[str, str]:
    """Return the endpoint map for the detected variant.

    Both HamClock and OpenHamClock currently use the same endpoint paths.
    This function exists to handle future divergence without changing
    the collector code.

    Returns:
        Dict mapping logical names to URL paths.
    """
    # Base endpoints (shared between both variants)
    endpoints = {
        "system": "/get_sys.txt",
        "spacewx": "/get_spacewx.txt",
        "band_conditions": "/get_bc.txt",
        "voacap": "/get_voacap.txt",
        "de": "/get_de.txt",
        "dx": "/get_dx.txt",
        "dxspots": "/get_dxspots.txt",
    }

    # OpenHamClock may add endpoints in the future
    if variant == "openhamclock":
        endpoints["config"] = "/get_config.txt"

    return endpoints
