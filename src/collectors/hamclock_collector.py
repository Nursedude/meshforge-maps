"""
MeshForge Maps - HamClock / Propagation Data Collector

Collects space weather and radio propagation overlay data from public sources.
HamClock (by WB0OEW) / OpenHamClock provide ham radio dashboard data.

Data sources:
  1. NOAA Space Weather Prediction Center (SWPC) - solar/geomagnetic data
  2. VOACAP - HF propagation predictions
  3. Local HamClock REST API (port 8080) if running
  4. PSKReporter - FT8/digital mode activity spots

This collector focuses on propagation-relevant data that overlays on the map:
  - Solar terminator (day/night boundary)
  - Band conditions summary
  - Solar flux / K-index / A-index
  - DRAP (D-Region Absorption Prediction) status

Note: Original HamClock ceases operation June 2026.
OpenHamClock (https://github.com/accius/openhamclock) is the successor.
"""

import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import BaseCollector, make_feature_collection

logger = logging.getLogger(__name__)

# NOAA SWPC endpoints (public JSON APIs)
SWPC_SOLAR_WIND = "https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json"
SWPC_KP_INDEX = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
SWPC_SOLAR_FLUX = "https://services.swpc.noaa.gov/products/summary/10cm-flux.json"
SWPC_A_INDEX = "https://services.swpc.noaa.gov/products/summary/solar-wind-mag-field.json"
SWPC_XRAY_FLUX = "https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json"

# Local HamClock API
HAMCLOCK_API = "http://localhost:8080"


class HamClockCollector(BaseCollector):
    """Collects space weather and propagation data for map overlays."""

    source_name = "hamclock"

    def __init__(
        self,
        hamclock_host: str = "localhost",
        hamclock_port: int = 8080,
        cache_ttl_seconds: int = 900,
    ):
        super().__init__(cache_ttl_seconds)
        self._hamclock_api = f"http://{hamclock_host}:{hamclock_port}"

    def _fetch(self) -> Dict[str, Any]:
        """Collect propagation and space weather data."""
        space_weather = self._fetch_space_weather()
        hamclock_data = self._fetch_hamclock_local()
        terminator = self._calculate_solar_terminator()

        # HamClock data is overlay metadata, not point features.
        # We store it in the FeatureCollection properties.
        fc = make_feature_collection([], self.source_name)
        fc["properties"]["space_weather"] = space_weather
        fc["properties"]["solar_terminator"] = terminator
        if hamclock_data:
            fc["properties"]["hamclock"] = hamclock_data
        return fc

    def _fetch_space_weather(self) -> Dict[str, Any]:
        """Fetch current space weather conditions from NOAA SWPC."""
        weather: Dict[str, Any] = {
            "solar_flux": None,
            "kp_index": None,
            "solar_wind_speed": None,
            "xray_flux": None,
            "band_conditions": "unknown",
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        # Solar flux (10.7cm / 2800 MHz)
        sfi = self._fetch_json(SWPC_SOLAR_FLUX)
        if sfi:
            weather["solar_flux"] = sfi.get("Flux")

        # Planetary K-index
        kp = self._fetch_json(SWPC_KP_INDEX)
        if kp and isinstance(kp, list) and len(kp) > 1:
            # Last entry in the list is most recent
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

        # Derive band conditions from Kp and SFI
        weather["band_conditions"] = self._assess_band_conditions(
            weather.get("solar_flux"), weather.get("kp_index")
        )

        return weather

    def _assess_band_conditions(
        self, sfi: Any, kp: Any
    ) -> str:
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

    def _calculate_solar_terminator(self) -> Dict[str, Any]:
        """Calculate the current solar terminator (day/night boundary).

        Returns the subsolar point and terminator metadata.
        The actual terminator line rendering happens client-side.
        """
        now = datetime.now(timezone.utc)
        day_of_year = now.timetuple().tm_yday
        hour_utc = now.hour + now.minute / 60.0

        # Solar declination (approximate)
        declination = -23.44 * math.cos(math.radians(360 / 365 * (day_of_year + 10)))

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

    def _fetch_hamclock_local(self) -> Optional[Dict[str, Any]]:
        """Try to fetch data from a local HamClock instance."""
        try:
            # Try the de (home station) endpoint
            data = self._fetch_json(f"{self._hamclock_api}/get_de.txt")
            if data:
                return {"de_station": data, "available": True}
        except Exception:
            pass
        return None

    def _fetch_json(self, url: str) -> Any:
        """Fetch JSON from a URL with timeout."""
        try:
            req = Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "MeshForge-Maps/0.1",
            })
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except (URLError, OSError, json.JSONDecodeError, ValueError) as e:
            logger.debug("Failed to fetch %s: %s", url, e)
            return None
