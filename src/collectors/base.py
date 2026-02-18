"""
MeshForge Maps - Base Collector

Abstract base class for all data source collectors.
Each collector outputs standardized GeoJSON FeatureCollections.
"""

import logging
import math
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils.reconnect import ReconnectStrategy

logger = logging.getLogger(__name__)

# Common data directory and unified cache path (shared across all collectors)
MESHFORGE_DATA_DIR = Path.home() / ".local" / "share" / "meshforge"
UNIFIED_CACHE_PATH = MESHFORGE_DATA_DIR / "node_cache.json"


def validate_coordinates(
    lat: Any, lon: Any, convert_int: bool = False
) -> Optional[Tuple[float, float]]:
    """Validate and normalize GPS coordinates.

    Handles NaN, Infinity, int-to-float conversion (latitudeI = lat * 1e7),
    and out-of-range values. Returns (lat, lon) tuple or None if invalid.
    """
    if lat is None or lon is None:
        return None

    # Integer coordinate conversion (Meshtastic latitudeI format)
    if convert_int:
        if isinstance(lat, int) and abs(lat) > 900:
            lat = lat / 1e7
        if isinstance(lon, int) and abs(lon) > 1800:
            lon = lon / 1e7

    try:
        lat = float(lat)
        lon = float(lon)
    except (ValueError, TypeError):
        return None

    # Guard against NaN and Infinity
    if math.isnan(lat) or math.isnan(lon):
        return None
    if math.isinf(lat) or math.isinf(lon):
        return None

    # Range check
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    # Reject Null Island (0,0) -- common artifact from protobuf schema
    # mismatches or uninitialized GPS data
    if abs(lat) < 0.01 and abs(lon) < 0.01:
        return None

    return (lat, lon)


def make_feature(
    node_id: str,
    lat: float,
    lon: float,
    network: str,
    name: str = "",
    node_type: str = "",
    **extra_props: Any,
) -> Optional[Dict[str, Any]]:
    """Create a standardized GeoJSON Feature for a mesh node.

    Returns None if coordinates are invalid (NaN, Infinity, out of range).
    """
    coords = validate_coordinates(lat, lon)
    if coords is None:
        return None
    lat, lon = coords

    properties = {
        "id": node_id,
        "name": name or node_id,
        "network": network,
        "node_type": node_type,
        "is_online": extra_props.pop("is_online", None),
        "last_seen": extra_props.pop("last_seen", None),
        "hardware": extra_props.pop("hardware", None),
        "role": extra_props.pop("role", None),
        "battery": extra_props.pop("battery", None),
        "snr": extra_props.pop("snr", None),
        "rssi": extra_props.pop("rssi", None),
        "altitude": extra_props.pop("altitude", None),
        "description": extra_props.pop("description", None),
    }
    # Add any remaining extra properties
    properties.update(extra_props)
    # Strip None values
    properties = {k: v for k, v in properties.items() if v is not None}

    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [lon, lat],
        },
        "properties": properties,
    }


def make_feature_collection(
    features: List[Dict[str, Any]],
    source: str,
    collected_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Wrap features in a GeoJSON FeatureCollection with metadata."""
    if collected_at is None:
        collected_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "source": source,
            "collected_at": collected_at,
            "node_count": len(features),
        },
    }


def deduplicate_features(
    feature_lists: List[List[Dict[str, Any]]],
    allow_no_id: bool = True,
) -> List[Dict[str, Any]]:
    """Merge multiple feature lists, deduplicating by feature ID.

    Features are deduplicated by their ``properties.id`` field. The first
    occurrence of each ID wins. Features without an ID are included
    unconditionally when *allow_no_id* is True.
    """
    result: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for features in feature_lists:
        for feature in features:
            if feature is None:
                continue
            fid = feature.get("properties", {}).get("id")
            if fid:
                if fid not in seen_ids:
                    seen_ids.add(fid)
                    result.append(feature)
            elif allow_no_id:
                result.append(feature)
    return result


class BaseCollector(ABC):
    """Abstract base for data source collectors.

    Supports retry with exponential backoff before falling back to
    stale cache.
    """

    source_name: str = "unknown"

    def __init__(
        self,
        cache_ttl_seconds: int = 900,
        max_retries: int = 0,
    ):
        self._cache_lock = threading.Lock()
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_time: float = 0
        self._cache_ttl = cache_ttl_seconds
        self._max_retries = max_retries
        self._last_error: Optional[str] = None
        self._last_error_time: float = 0
        self._last_success_time: float = 0
        self._total_collections: int = 0
        self._total_errors: int = 0

    def collect(self) -> Dict[str, Any]:
        """Collect data, using cache if fresh enough.

        Retries with exponential backoff before falling back to stale cache.
        """
        now = time.time()
        with self._cache_lock:
            if self._cache and (now - self._cache_time) < self._cache_ttl:
                logger.debug("%s: returning cached data", self.source_name)
                return self._cache

        # Retry loop with backoff (single strategy instance preserves escalating delays)
        last_error: Optional[Exception] = None
        attempts = 1 + self._max_retries
        strategy = ReconnectStrategy.for_collector()

        for attempt in range(attempts):
            try:
                data = self._fetch()
                with self._cache_lock:
                    self._cache = data
                    self._cache_time = time.time()
                self._last_success_time = time.time()
                self._total_collections += 1
                count = len(data.get("features", []))
                if attempt > 0:
                    logger.info(
                        "%s: collected %d nodes (after %d retries)",
                        self.source_name,
                        count,
                        attempt,
                    )
                else:
                    logger.info(
                        "%s: collected %d nodes", self.source_name, count
                    )
                return data
            except Exception as e:
                last_error = e
                if attempt < self._max_retries:
                    delay = strategy.next_delay()
                    logger.debug(
                        "%s: attempt %d failed (%s), retrying in %.1fs",
                        self.source_name,
                        attempt + 1,
                        e,
                        delay,
                    )
                    time.sleep(delay)

        # All attempts failed
        self._last_error = str(last_error) if last_error else "unknown error"
        self._last_error_time = time.time()
        self._total_errors += 1
        logger.error("%s: collection failed: %s", self.source_name, last_error)
        with self._cache_lock:
            if self._cache:
                logger.warning("%s: returning stale cache", self.source_name)
                return self._cache
        return make_feature_collection([], self.source_name)

    @abstractmethod
    def _fetch(self) -> Dict[str, Any]:
        """Fetch fresh data from the source. Returns a GeoJSON FeatureCollection."""
        ...

    @property
    def health_info(self) -> Dict[str, Any]:
        """Return collector health info for status reporting."""
        now = time.time()
        with self._cache_lock:
            has_cache = self._cache is not None
        info: Dict[str, Any] = {
            "source": self.source_name,
            "total_collections": self._total_collections,
            "total_errors": self._total_errors,
            "has_cache": has_cache,
        }
        if self._last_success_time:
            info["last_success_age_seconds"] = int(now - self._last_success_time)
        if self._last_error:
            info["last_error"] = self._last_error
            info["last_error_age_seconds"] = int(now - self._last_error_time)
        return info

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache = None
            self._cache_time = 0
