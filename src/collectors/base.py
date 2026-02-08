"""
MeshForge Maps - Base Collector

Abstract base class for all data source collectors.
Each collector outputs standardized GeoJSON FeatureCollections.
"""

import logging
import math
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from ..utils.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


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


class BaseCollector(ABC):
    """Abstract base for data source collectors.

    Supports optional circuit breaker integration and retry with
    exponential backoff before falling back to stale cache.
    """

    source_name: str = "unknown"

    def __init__(
        self,
        cache_ttl_seconds: int = 900,
        circuit_breaker: Optional["CircuitBreaker"] = None,
        max_retries: int = 0,
    ):
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_time: float = 0
        self._cache_ttl = cache_ttl_seconds
        self._circuit_breaker = circuit_breaker
        self._max_retries = max_retries

    @property
    def circuit_breaker(self) -> Optional["CircuitBreaker"]:
        return self._circuit_breaker

    @circuit_breaker.setter
    def circuit_breaker(self, cb: Optional["CircuitBreaker"]) -> None:
        self._circuit_breaker = cb

    def collect(self) -> Dict[str, Any]:
        """Collect data, using cache if fresh enough.

        If a circuit breaker is attached and OPEN, skips the fetch entirely
        and returns cached data. Retries with exponential backoff before
        falling back to stale cache.
        """
        now = time.time()
        if self._cache and (now - self._cache_time) < self._cache_ttl:
            logger.debug("%s: returning cached data", self.source_name)
            return self._cache

        # Circuit breaker check: skip fetch if circuit is open
        cb = self._circuit_breaker
        if cb and not cb.can_execute():
            logger.debug(
                "%s: circuit breaker OPEN, skipping fetch", self.source_name
            )
            if self._cache:
                return self._cache
            return make_feature_collection([], self.source_name)

        # Retry loop with backoff
        last_error: Optional[Exception] = None
        attempts = 1 + self._max_retries

        for attempt in range(attempts):
            try:
                data = self._fetch()
                self._cache = data
                self._cache_time = time.time()
                count = len(data.get("features", []))
                if cb:
                    cb.record_success()
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
                    from ..utils.reconnect import ReconnectStrategy

                    delay = ReconnectStrategy.for_collector().next_delay()
                    logger.debug(
                        "%s: attempt %d failed (%s), retrying in %.1fs",
                        self.source_name,
                        attempt + 1,
                        e,
                        delay,
                    )
                    time.sleep(delay)

        # All attempts failed
        if cb:
            cb.record_failure()
        logger.error("%s: collection failed: %s", self.source_name, last_error)
        if self._cache:
            logger.warning("%s: returning stale cache", self.source_name)
            return self._cache
        return make_feature_collection([], self.source_name)

    @abstractmethod
    def _fetch(self) -> Dict[str, Any]:
        """Fetch fresh data from the source. Returns a GeoJSON FeatureCollection."""
        ...

    def clear_cache(self) -> None:
        self._cache = None
        self._cache_time = 0
