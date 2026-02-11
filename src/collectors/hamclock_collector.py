"""
MeshForge Maps - HamClock / Propagation Data Collector (API-Only)

Collects space weather and radio propagation overlay data.
Architecture aligned with meshforge core (commands/propagation.py):

    PRIMARY: HamClock/OpenHamClock REST API (when available)
    FALLBACK: NOAA SWPC public JSON APIs (always works)

HamClock REST API endpoints (key=value text format):
    get_sys.txt      — System info / connection test
    get_spacewx.txt  — SFI, Kp, A-index, X-ray, SSN, proton, aurora
    get_bc.txt       — HF band conditions (80m-10m)
    get_voacap.txt   — VOACAP propagation predictions
    get_de.txt       — Home (DE) location
    get_dx.txt       — Target (DX) location
    get_dxspots.txt  — DX cluster spots

Solar terminator is always computed locally (no external dependency).

Original HamClock (WB0OEW): ceases operation June 2026.
OpenHamClock: https://github.com/accius/openhamclock (MIT, port 3000).
"""

import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import BaseCollector, make_feature_collection
from .. import __version__
from ..utils.openhamclock_compat import (
    detect_variant,
    get_endpoint_map,
    normalize_band_conditions,
    normalize_de_dx,
    normalize_spacewx,
)

logger = logging.getLogger(__name__)

# User-Agent string for HTTP requests
_USER_AGENT = f"MeshForge-Maps/{__version__}"

# NOAA SWPC endpoints (fallback when HamClock is unavailable)
SWPC_SOLAR_FLUX = "https://services.swpc.noaa.gov/products/summary/10cm-flux.json"
SWPC_KP_INDEX = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
SWPC_SOLAR_WIND = "https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json"


def _parse_key_value(data: str) -> Dict[str, str]:
    """Parse HamClock key=value text response into a dict."""
    result = {}
    for line in data.strip().split("\n"):
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


class HamClockCollector(BaseCollector):
    """Collects space weather and propagation data for map overlays.

    API-only architecture: tries HamClock REST API first, falls back
    to NOAA SWPC if HamClock is unavailable.

    Port auto-detection:
        1. Configured port (default 8080, original HamClock)
        2. OpenHamClock port (default 3000, MIT successor)
    """

    source_name = "hamclock"

    # OpenHamClock default port (community successor, MIT license)
    OPENHAMCLOCK_DEFAULT_PORT = 3000

    def __init__(
        self,
        hamclock_host: str = "localhost",
        hamclock_port: int = 8080,
        openhamclock_port: int = OPENHAMCLOCK_DEFAULT_PORT,
        cache_ttl_seconds: int = 900,
    ):
        super().__init__(cache_ttl_seconds)
        self._hamclock_host = hamclock_host
        self._hamclock_port = hamclock_port
        self._openhamclock_port = openhamclock_port
        self._hamclock_api = f"http://{hamclock_host}:{hamclock_port}"
        self._hamclock_available: Optional[bool] = None
        # Tracks which variant was detected ("hamclock", "openhamclock", or None)
        self._detected_variant: Optional[str] = None
        # Endpoint map (updated when variant is detected)
        self._endpoints = get_endpoint_map("hamclock")

    # ==================== Public API ====================

    def is_hamclock_available(self) -> bool:
        """Test if OpenHamClock or HamClock REST API is reachable.

        Tries OpenHamClock port first (default 3000, community successor),
        then falls back to HamClock legacy port (default 8080).
        Updates _hamclock_api to whichever responds.
        Uses detect_variant() to identify which variant is running.
        """
        # Try OpenHamClock port first (community successor, actively developed)
        if self._openhamclock_port != self._hamclock_port:
            openhamclock_url = f"http://{self._hamclock_host}:{self._openhamclock_port}"
            raw = self._fetch_text(f"{openhamclock_url}/get_sys.txt")
            if raw is not None and len(raw) > 0:
                self._hamclock_api = openhamclock_url
                self._hamclock_available = True
                self._detected_variant = detect_variant(raw)
                self._endpoints = get_endpoint_map(self._detected_variant)
                return True

        # Fall back to HamClock legacy port
        legacy_url = f"http://{self._hamclock_host}:{self._hamclock_port}"
        raw = self._fetch_text(f"{legacy_url}/get_sys.txt")
        if raw is not None and len(raw) > 0:
            self._hamclock_api = legacy_url
            self._hamclock_available = True
            self._detected_variant = detect_variant(raw)
            self._endpoints = get_endpoint_map(self._detected_variant)
            if self._openhamclock_port != self._hamclock_port:
                logger.info(
                    "%s detected on legacy port %d (OpenHamClock port %d unavailable)",
                    self._detected_variant,
                    self._hamclock_port,
                    self._openhamclock_port,
                )
            return True

        self._hamclock_available = False
        self._detected_variant = None
        self._endpoints = get_endpoint_map("hamclock")
        return False

    # ==================== Core Fetch ====================

    def _fetch(self) -> Dict[str, Any]:
        """Collect propagation and space weather data.

        Strategy: try HamClock API first, fall back to NOAA SWPC.
        Solar terminator is always computed locally.
        """
        # Check HamClock availability (cached for this collection cycle)
        hamclock_up = self.is_hamclock_available()

        if hamclock_up:
            space_weather = self._fetch_space_weather_hamclock()
            band_conditions = self._fetch_band_conditions_hamclock()
            voacap = self._fetch_voacap()
            de_info = self._fetch_de()
            dx_info = self._fetch_dx()
            dxspots = self._fetch_dxspots()
        else:
            space_weather = self._fetch_space_weather_noaa()
            band_conditions = None
            voacap = None
            de_info = None
            dx_info = None
            dxspots = None

        terminator = self._calculate_solar_terminator()

        # Build the FeatureCollection with overlay metadata
        fc = make_feature_collection([], self.source_name)
        fc["properties"]["space_weather"] = space_weather
        fc["properties"]["solar_terminator"] = terminator

        # Determine source label based on detected variant
        if hamclock_up and self._detected_variant == "openhamclock":
            source_label = "OpenHamClock API"
            active_port = self._openhamclock_port
        elif hamclock_up:
            source_label = "HamClock API"
            active_port = self._hamclock_port
        else:
            source_label = "NOAA SWPC"
            active_port = self._hamclock_port

        hamclock_data: Dict[str, Any] = {
            "available": hamclock_up,
            "source": source_label,
            "variant": self._detected_variant,
            "host": self._hamclock_host,
            "port": active_port,
        }
        if band_conditions:
            hamclock_data["band_conditions"] = band_conditions
        if voacap:
            hamclock_data["voacap"] = voacap
        if de_info:
            hamclock_data["de_station"] = de_info
        if dx_info:
            hamclock_data["dx_station"] = dx_info
        if dxspots:
            hamclock_data["dxspots"] = dxspots

        fc["properties"]["hamclock"] = hamclock_data
        return fc

    # ==================== HamClock API Methods ====================

    def _fetch_space_weather_hamclock(self) -> Dict[str, Any]:
        """Fetch space weather from HamClock REST API (get_spacewx.txt)."""
        source_label = "OpenHamClock API" if self._detected_variant == "openhamclock" else "HamClock API"
        weather: Dict[str, Any] = {
            "source": source_label,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        endpoint = self._endpoints.get("spacewx", "/get_spacewx.txt")
        raw = self._fetch_text(f"{self._hamclock_api}{endpoint}")
        if not raw:
            return weather

        parsed = normalize_spacewx(_parse_key_value(raw))

        # Map HamClock response keys to standard names
        # HamClock returns keys like: SFI, Kp, A, Xray, SSN, Proton, Aurora
        key_map = {
            "solar_flux": ["sfi", "flux"],
            "kp_index": ["kp"],
            "a_index": ["a", "a_index"],
            "xray_flux": ["xray", "x-ray"],
            "ssn": ["ssn", "sunspot", "sunspots"],
            "proton_flux": ["proton", "pf"],
            "aurora": ["aurora", "aur"],
        }

        for standard_key, possible_keys in key_map.items():
            for raw_key, value in parsed.items():
                if raw_key.lower() in possible_keys:
                    weather[standard_key] = value
                    break

        # Derive band conditions from Kp + SFI
        weather["band_conditions"] = self._assess_band_conditions(
            weather.get("solar_flux"), weather.get("kp_index")
        )

        return weather

    def _fetch_band_conditions_hamclock(self) -> Optional[Dict[str, Any]]:
        """Fetch HF band conditions from HamClock (get_bc.txt)."""
        endpoint = self._endpoints.get("band_conditions", "/get_bc.txt")
        raw = self._fetch_text(f"{self._hamclock_api}{endpoint}")
        if not raw:
            return None

        parsed = _parse_key_value(raw)
        bands: Dict[str, str] = {}

        for key, value in parsed.items():
            key_lower = key.lower()
            if "80" in key_lower or "40" in key_lower:
                bands["80m-40m"] = value
            elif "30" in key_lower or "20" in key_lower:
                bands["30m-20m"] = value
            elif "17" in key_lower or "15" in key_lower:
                bands["17m-15m"] = value
            elif "12" in key_lower or "10" in key_lower:
                bands["12m-10m"] = value

        return {"bands": bands, "raw": parsed} if bands else None

    def _fetch_voacap(self) -> Optional[Dict[str, Any]]:
        """Fetch VOACAP propagation predictions from HamClock (get_voacap.txt)."""
        endpoint = self._endpoints.get("voacap", "/get_voacap.txt")
        raw = self._fetch_text(f"{self._hamclock_api}{endpoint}")
        if not raw:
            return None

        voacap: Dict[str, Any] = {"path": "", "utc": "", "bands": {}}

        for line in raw.strip().split("\n"):
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lower()
            value = value.strip()

            if key == "path":
                voacap["path"] = value
            elif key == "utc":
                voacap["utc"] = value
            elif "m" in key:
                # Band data: "80m=23,12" -> reliability=23%, SNR=12dB
                try:
                    if "," in value:
                        rel, snr = value.split(",", 1)
                        voacap["bands"][key] = {
                            "reliability": int(rel.strip()),
                            "snr": int(snr.strip()),
                            "status": self._reliability_to_status(int(rel.strip())),
                        }
                    else:
                        rel = int(value)
                        voacap["bands"][key] = {
                            "reliability": rel,
                            "snr": 0,
                            "status": self._reliability_to_status(rel),
                        }
                except ValueError:
                    logger.debug("Could not parse VOACAP band %s: %s", key, value)

        # Calculate best band
        best_band = None
        best_rel = 0
        for band, data in voacap["bands"].items():
            if data["reliability"] > best_rel:
                best_rel = data["reliability"]
                best_band = band
        voacap["best_band"] = best_band
        voacap["best_reliability"] = best_rel

        return voacap if voacap["bands"] else None

    def _fetch_de(self) -> Optional[Dict[str, str]]:
        """Fetch home (DE) location from HamClock (get_de.txt)."""
        endpoint = self._endpoints.get("de", "/get_de.txt")
        raw = self._fetch_text(f"{self._hamclock_api}{endpoint}")
        if not raw:
            return None
        parsed = normalize_de_dx(_parse_key_value(raw))
        return {
            "lat": parsed.get("lat", ""),
            "lon": parsed.get("lng", parsed.get("lon", "")),
            "grid": parsed.get("grid", ""),
            "call": parsed.get("call", ""),
        }

    def _fetch_dx(self) -> Optional[Dict[str, str]]:
        """Fetch target (DX) location from HamClock (get_dx.txt)."""
        endpoint = self._endpoints.get("dx", "/get_dx.txt")
        raw = self._fetch_text(f"{self._hamclock_api}{endpoint}")
        if not raw:
            return None
        parsed = normalize_de_dx(_parse_key_value(raw))
        return {
            "lat": parsed.get("lat", ""),
            "lon": parsed.get("lng", parsed.get("lon", "")),
            "grid": parsed.get("grid", ""),
            "call": parsed.get("call", ""),
        }

    def _fetch_dxspots(self) -> Optional[list]:
        """Fetch DX cluster spots from HamClock (get_dxspots.txt).

        Returns a list of spot dicts with call, freq, de, utc fields,
        or None if unavailable.
        """
        endpoint = self._endpoints.get("dxspots", "/get_dxspots.txt")
        raw = self._fetch_text(f"{self._hamclock_api}{endpoint}")
        if not raw:
            return None

        spots: list = []
        for line in raw.strip().split("\n"):
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lower()
            value = value.strip()

            # DX spots come as indexed entries: Spot0=call freq de utc ...
            if key.startswith("spot"):
                parts = value.split()
                if len(parts) >= 3:
                    spot: Dict[str, Any] = {
                        "dx_call": parts[0],
                        "freq_khz": parts[1],
                    }
                    if len(parts) >= 3:
                        spot["de_call"] = parts[2]
                    if len(parts) >= 4:
                        spot["utc"] = parts[3]
                    if len(parts) >= 5:
                        spot["comment"] = " ".join(parts[4:])
                    spots.append(spot)

        return spots if spots else None

    # ==================== Public Query Methods ====================

    def get_hamclock_data(self) -> Dict[str, Any]:
        """Return the latest HamClock data without a full collection cycle.

        Intended for TUI tools and the /api/hamclock endpoint.
        Uses cached data if fresh, otherwise performs a lightweight fetch.
        """
        fc = self.collect()
        props = fc.get("properties", {})
        hamclock = props.get("hamclock", {})
        result: Dict[str, Any] = {
            "available": hamclock.get("available", False),
            "source": hamclock.get("source", "unknown"),
            "host": self._hamclock_host,
            "port": self._hamclock_port,
        }
        # Space weather
        sw = props.get("space_weather", {})
        if sw:
            result["space_weather"] = sw
        # Solar terminator
        term = props.get("solar_terminator", {})
        if term:
            result["solar_terminator"] = term
        # HamClock-specific data
        for key in ("band_conditions", "voacap", "de_station", "dx_station", "dxspots"):
            if key in hamclock:
                result[key] = hamclock[key]
        return result

    # ==================== NOAA Fallback ====================

    def _fetch_space_weather_noaa(self) -> Dict[str, Any]:
        """Fetch space weather from NOAA SWPC (fallback when HamClock unavailable)."""
        weather: Dict[str, Any] = {
            "solar_flux": None,
            "kp_index": None,
            "solar_wind_speed": None,
            "band_conditions": "unknown",
            "source": "NOAA SWPC",
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        # Solar flux (10.7cm / 2800 MHz)
        sfi = self._fetch_json(SWPC_SOLAR_FLUX)
        if sfi:
            weather["solar_flux"] = sfi.get("Flux")

        # Planetary K-index
        kp = self._fetch_json(SWPC_KP_INDEX)
        if kp and isinstance(kp, list) and len(kp) > 1:
            latest = kp[-1]
            if isinstance(latest, list) and len(latest) >= 2:
                try:
                    weather["kp_index"] = float(latest[1])
                except (ValueError, TypeError):
                    pass

        # Solar wind speed
        sw = self._fetch_json(SWPC_SOLAR_WIND)
        if sw:
            weather["solar_wind_speed"] = sw.get("WindSpeed")

        # Derive band conditions
        weather["band_conditions"] = self._assess_band_conditions(
            weather.get("solar_flux"), weather.get("kp_index")
        )

        return weather

    # ==================== Shared Helpers ====================

    def _assess_band_conditions(self, sfi: Any, kp: Any) -> str:
        """Simple band condition assessment from SFI and Kp."""
        try:
            sfi_val = float(sfi) if sfi else None
            kp_val = float(kp) if kp else None
        except (ValueError, TypeError):
            return "unknown"

        if sfi_val is None or kp_val is None:
            return "unknown"

        if kp_val >= 7:
            return "poor"  # Major geomagnetic storm
        if kp_val >= 5:
            return "fair"  # Minor storm
        if sfi_val >= 150 and kp_val < 4:
            return "excellent"
        if sfi_val >= 100 and kp_val < 4:
            return "good"
        if sfi_val >= 70:
            return "fair"
        return "poor"

    @staticmethod
    def _reliability_to_status(reliability: int) -> str:
        """Convert VOACAP reliability percentage to status string."""
        if reliability >= 80:
            return "excellent"
        if reliability >= 60:
            return "good"
        if reliability >= 40:
            return "fair"
        if reliability > 0:
            return "poor"
        return "closed"

    def _calculate_solar_terminator(self) -> Dict[str, Any]:
        """Calculate the current solar terminator (day/night boundary).

        Returns the subsolar point and terminator metadata.
        The actual terminator line rendering happens client-side.
        """
        now = datetime.now(timezone.utc)
        day_of_year = now.timetuple().tm_yday
        hour_utc = now.hour + now.minute / 60.0

        # Solar declination (approximate)
        declination = -23.44 * math.cos(
            math.radians(360 / 365 * (day_of_year + 10))
        )

        # Subsolar longitude (moves 15 deg/hour westward from noon)
        subsolar_lon = (12.0 - hour_utc) * 15.0
        if subsolar_lon > 180:
            subsolar_lon -= 360
        elif subsolar_lon < -180:
            subsolar_lon += 360

        return {
            "subsolar_lat": declination,
            "subsolar_lon": subsolar_lon,
            "timestamp": now.isoformat(),
        }

    def _fetch_text(self, url: str) -> Optional[str]:
        """Fetch raw text from a URL with timeout."""
        try:
            req = Request(
                url,
                headers={"User-Agent": _USER_AGENT},
            )
            with urlopen(req, timeout=10) as resp:
                return resp.read().decode("utf-8")
        except (URLError, OSError, ValueError) as e:
            logger.debug("Failed to fetch %s: %s", url, e)
            return None

    def _fetch_json(self, url: str) -> Any:
        """Fetch JSON from a URL with timeout."""
        try:
            req = Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": _USER_AGENT,
                },
            )
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except (URLError, OSError, json.JSONDecodeError, ValueError) as e:
            logger.debug("Failed to fetch %s: %s", url, e)
            return None
