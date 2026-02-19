"""
MeshForge Maps - NOAA Weather Alert Collector

Fetches active weather alerts from the NOAA Weather API (api.weather.gov)
and returns them as a GeoJSON FeatureCollection with polygon geometries
for rendering as map overlays.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

from .base import BaseCollector, make_feature_collection

logger = logging.getLogger(__name__)

_USER_AGENT = "MeshForge-Maps/1.0 (mesh network mapping tool)"

# NOAA severity → display color mapping
SEVERITY_COLORS: Dict[str, str] = {
    "Extreme": "#d32f2f",
    "Severe": "#f44336",
    "Moderate": "#ff9800",
    "Minor": "#ffeb3b",
    "Unknown": "#9e9e9e",
}

# NOAA severity → numeric sort order (lower = more severe)
SEVERITY_ORDER: Dict[str, int] = {
    "Extreme": 0,
    "Severe": 1,
    "Moderate": 2,
    "Minor": 3,
    "Unknown": 4,
}


class NOAAAlertCollector(BaseCollector):
    """Collector for NOAA National Weather Service active alerts.

    Fetches from the NWS API (api.weather.gov/alerts/active) which returns
    native GeoJSON with Polygon/MultiPolygon geometries representing alert
    areas. Features without geometry (e.g. national-level alerts) are
    excluded since they cannot be rendered on the map.
    """

    source_name = "noaa_alerts"

    def __init__(
        self,
        api_url: str = "https://api.weather.gov/alerts/active",
        area: Optional[str] = None,
        severity_filter: Optional[List[str]] = None,
        cache_ttl_seconds: int = 300,
    ):
        super().__init__(cache_ttl_seconds=cache_ttl_seconds)
        self._base_url = api_url
        self._area = area
        self._severity_filter = severity_filter

    def _build_url(self) -> str:
        """Build the API URL with query parameters."""
        url = self._base_url
        params = []
        params.append("status=actual")
        params.append("message_type=alert,update")
        if self._area:
            params.append(f"area={self._area}")
        if self._severity_filter:
            params.append(f"severity={','.join(self._severity_filter)}")
        if params:
            url += "?" + "&".join(params)
        return url

    def _fetch(self) -> Dict[str, Any]:
        """Fetch active weather alerts from NOAA NWS API."""
        url = self._build_url()
        try:
            req = Request(url, headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/geo+json",
            })
            with urlopen(req, timeout=15) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except (URLError, OSError, json.JSONDecodeError, ValueError) as e:
            logger.debug("NOAA alert fetch failed: %s", e)
            return make_feature_collection([], self.source_name)

        features = self._process_features(raw.get("features", []))
        fc = make_feature_collection(features, self.source_name)
        fc["properties"]["alert_count"] = len(features)
        return fc

    def _process_features(
        self, raw_features: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Process NOAA GeoJSON features into standardized format.

        Filters out features without geometry and enriches properties
        with severity colors and sort order.
        """
        processed = []
        seen_ids: set = set()

        for feature in raw_features:
            geom = feature.get("geometry")
            if not geom:
                # Skip alerts without polygon geometry (national-level text alerts)
                continue

            props = feature.get("properties", {})
            alert_id = props.get("id", "")

            # Deduplicate by alert ID (updates can produce duplicates)
            if alert_id in seen_ids:
                continue
            seen_ids.add(alert_id)

            severity = props.get("severity", "Unknown")
            color = SEVERITY_COLORS.get(severity, SEVERITY_COLORS["Unknown"])

            # Check if alert has expired
            expires = props.get("expires")
            if expires:
                try:
                    from datetime import datetime, timezone
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    if exp_dt < datetime.now(timezone.utc):
                        continue
                except (ValueError, TypeError):
                    pass  # Keep alert if we can't parse expiry

            processed.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "id": alert_id,
                    "network": "noaa_alerts",
                    "event": props.get("event", ""),
                    "headline": props.get("headline", ""),
                    "description": props.get("description", ""),
                    "severity": severity,
                    "certainty": props.get("certainty", ""),
                    "urgency": props.get("urgency", ""),
                    "area_desc": props.get("areaDesc", ""),
                    "onset": props.get("onset"),
                    "expires": expires,
                    "sender_name": props.get("senderName", ""),
                    "color": color,
                    "severity_order": SEVERITY_ORDER.get(severity, 4),
                },
            })

        # Sort by severity (most severe first)
        processed.sort(key=lambda f: f["properties"]["severity_order"])
        return processed
