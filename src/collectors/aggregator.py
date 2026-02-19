"""
MeshForge Maps - Data Aggregator

Merges GeoJSON FeatureCollections from all enabled collectors
into a single unified collection with deduplication.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from .aredn_collector import AREDNCollector
from .base import deduplicate_features, make_feature_collection
from .hamclock_collector import HamClockCollector
from .meshtastic_collector import MeshtasticCollector
from .mqtt_subscriber import MQTTNodeStore, MQTTSubscriber
from .noaa_alert_collector import NOAAAlertCollector
from .reticulum_collector import ReticulumCollector
from ..utils.event_bus import EventBus
from ..utils.perf_monitor import PerfMonitor

logger = logging.getLogger(__name__)

# Default retry count for collectors (before cache fallback)
DEFAULT_COLLECTOR_RETRIES = 2


class DataAggregator:
    """Aggregates data from all enabled collectors into unified GeoJSON."""

    def __init__(self, config: Dict[str, Any]):
        self._config = config
        cache_ttl = config.get("cache_ttl_minutes", 15) * 60
        self._collectors = {}
        self._data_lock = threading.Lock()
        self._cached_overlay: Dict[str, Any] = {}
        self._last_collect_time: float = 0
        self._last_collect_counts: Dict[str, int] = {}

        # Event bus for decoupled real-time communication
        self._event_bus = EventBus()

        # Performance monitor for collection timing
        self._perf_monitor = PerfMonitor()

        retries = DEFAULT_COLLECTOR_RETRIES

        # Initialize live MQTT subscriber for Meshtastic
        # Supports private broker configuration (upstream: private MQTT support)
        self._mqtt_subscriber: Optional[MQTTSubscriber] = None
        mqtt_store: Optional[MQTTNodeStore] = None
        if config.get("enable_meshtastic", True):
            self._mqtt_subscriber = MQTTSubscriber(
                broker=config.get("mqtt_broker", "mqtt.meshtastic.org"),
                port=config.get("mqtt_port", 1883),
                topic=config.get("mqtt_topic", "msh/#"),
                username=config.get("mqtt_username"),
                password=config.get("mqtt_password"),
                event_bus=self._event_bus,
            )
            if self._mqtt_subscriber.available:
                self._mqtt_subscriber.start()
                mqtt_store = self._mqtt_subscriber.store
            else:
                self._mqtt_subscriber = None

        if config.get("enable_meshtastic", True):
            self._collectors["meshtastic"] = MeshtasticCollector(
                cache_ttl_seconds=cache_ttl,
                mqtt_store=mqtt_store,
                source_mode=config.get("meshtastic_source", "auto"),
            )
            self._collectors["meshtastic"]._max_retries = retries

        if config.get("enable_reticulum", True):
            self._collectors["reticulum"] = ReticulumCollector(
                cache_ttl_seconds=cache_ttl
            )
            self._collectors["reticulum"]._max_retries = retries

        if config.get("enable_hamclock", True):
            self._collectors["hamclock"] = HamClockCollector(
                hamclock_host=config.get("hamclock_host", "localhost"),
                hamclock_port=config.get("hamclock_port", 8080),
                openhamclock_port=config.get("openhamclock_port", 3000),
                cache_ttl_seconds=cache_ttl,
            )
            self._collectors["hamclock"]._max_retries = retries

        if config.get("enable_aredn", True):
            self._collectors["aredn"] = AREDNCollector(
                cache_ttl_seconds=cache_ttl
            )
            self._collectors["aredn"]._max_retries = retries

        # NOAA weather alerts (polygon overlay — not included in collect_all)
        if config.get("enable_noaa_alerts", True):
            self._collectors["noaa_alerts"] = NOAAAlertCollector(
                area=config.get("noaa_alerts_area"),
                severity_filter=config.get("noaa_alerts_severity"),
                cache_ttl_seconds=min(cache_ttl, 300),  # Cap at 5 min for alerts
            )
            self._collectors["noaa_alerts"]._max_retries = retries

    # Collectors that return polygon/overlay data — excluded from collect_all()
    # because their features are not mesh node points.
    _OVERLAY_ONLY_COLLECTORS = {"noaa_alerts"}

    def collect_all(self) -> Dict[str, Any]:
        """Collect from all enabled sources and merge into one FeatureCollection."""
        per_source_features: List[List[Dict[str, Any]]] = []
        source_counts: Dict[str, int] = {}
        overlay_data: Dict[str, Any] = {}

        with self._perf_monitor.time_cycle() as cycle_ctx:
            for name, collector in self._collectors.items():
                if name in self._OVERLAY_ONLY_COLLECTORS:
                    continue
                try:
                    with self._perf_monitor.time_collection(name) as src_ctx:
                        fc = collector.collect()
                        features = fc.get("features", [])
                        source_counts[name] = len(features)
                        src_ctx.node_count = len(features)
                        # Detect cache hit from collector's cache state
                        with collector._cache_lock:
                            src_ctx.from_cache = (
                                collector._cache is not None
                                and fc is collector._cache
                            )

                    per_source_features.append(features)

                    # Capture overlay data (space weather, terminator, etc.)
                    fc_props = fc.get("properties", {})
                    for key in ("space_weather", "solar_terminator", "hamclock"):
                        if key in fc_props:
                            overlay_data[key] = fc_props[key]

                except Exception as e:
                    logger.error("Collector %s failed: %s", name, e)
                    source_counts[name] = 0

            all_features = deduplicate_features(per_source_features, allow_no_id=True)
            cycle_ctx.node_count = len(all_features)

        # Cache overlay data so /api/overlay doesn't trigger a full re-collect
        with self._data_lock:
            self._cached_overlay = overlay_data
            self._last_collect_time = time.time()
            self._last_collect_counts = dict(source_counts)

        result = make_feature_collection(all_features, "aggregated")
        result["properties"]["sources"] = source_counts
        result["properties"]["total_nodes"] = len(all_features)
        result["properties"]["enabled_sources"] = list(self._collectors.keys())
        result["properties"]["overlay_data"] = overlay_data

        logger.info(
            "Aggregated %d nodes from %d sources: %s",
            len(all_features),
            len(self._collectors),
            source_counts,
        )
        return result

    def collect_source(self, source_name: str) -> Dict[str, Any]:
        """Collect from a single named source."""
        collector = self._collectors.get(source_name)
        if not collector:
            return make_feature_collection([], source_name)
        return collector.collect()

    def get_topology_links(self) -> List[Dict[str, Any]]:
        """Get topology link data from MQTT subscriber and AREDN LQM."""
        links = []
        if self._mqtt_subscriber:
            links.extend(self._mqtt_subscriber.store.get_topology_links())
        # Include AREDN LQM topology links
        aredn = self._collectors.get("aredn")
        if aredn and hasattr(aredn, "get_topology_links"):
            links.extend(aredn.get_topology_links())
        return links

    def get_topology_geojson(self) -> Dict[str, Any]:
        """Get topology as GeoJSON FeatureCollection with SNR-colored edges.

        Combines Meshtastic MQTT topology with AREDN LQM topology links.
        """
        from .mqtt_subscriber import _classify_snr

        # Start with MQTT topology GeoJSON if available
        if self._mqtt_subscriber:
            result = self._mqtt_subscriber.store.get_topology_geojson()
        else:
            result = {"type": "FeatureCollection", "features": [], "properties": {"link_count": 0}}

        # Add AREDN LQM links as GeoJSON features
        aredn = self._collectors.get("aredn")
        if aredn and hasattr(aredn, "get_topology_links"):
            for link in aredn.get_topology_links():
                # Only include links with resolved coordinates
                if "source_lat" not in link or "target_lat" not in link:
                    continue
                snr = link.get("snr")
                quality_label, color = _classify_snr(snr)
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [link["source_lon"], link["source_lat"]],
                            [link["target_lon"], link["target_lat"]],
                        ],
                    },
                    "properties": {
                        "source": link.get("source", ""),
                        "target": link.get("target", ""),
                        "snr": snr,
                        "quality": quality_label,
                        "color": color,
                        "network": "aredn",
                        "link_type": link.get("link_type", ""),
                        "aredn_quality": link.get("quality"),
                    },
                }
                result["features"].append(feature)

        result["properties"]["link_count"] = len(result["features"])
        return result

    def get_cached_overlay(self) -> Dict[str, Any]:
        """Return cached overlay data from the last collect_all() call.

        Falls back to collecting from hamclock only if no cache exists,
        avoiding a full multi-source aggregation.
        """
        with self._data_lock:
            if self._cached_overlay:
                return dict(self._cached_overlay)
        # No cache yet -- collect overlay from hamclock only
        hamclock = self._collectors.get("hamclock")
        if hamclock:
            try:
                fc = hamclock.collect()
                fc_props = fc.get("properties", {})
                overlay: Dict[str, Any] = {}
                for key in ("space_weather", "solar_terminator", "hamclock"):
                    if key in fc_props:
                        overlay[key] = fc_props[key]
                with self._data_lock:
                    self._cached_overlay = overlay
                return overlay
            except Exception as e:
                logger.error("Overlay-only collection failed: %s", e)
        return {}

    @property
    def last_collect_age_seconds(self) -> Optional[float]:
        """Seconds since last successful collect_all(), or None if never collected."""
        with self._data_lock:
            t = self._last_collect_time
        if t == 0:
            return None
        return time.time() - t

    @property
    def last_collect_counts(self) -> Dict[str, int]:
        with self._data_lock:
            return dict(self._last_collect_counts)

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def perf_monitor(self) -> PerfMonitor:
        return self._perf_monitor

    def get_source_health(self) -> Dict[str, Any]:
        """Return per-source health info for all collectors."""
        health: Dict[str, Any] = {}
        for name, collector in self._collectors.items():
            health[name] = collector.health_info
        return health

    def get_collector(self, name: str) -> Optional[Any]:
        """Return a named collector, or None if not enabled."""
        return self._collectors.get(name)

    @property
    def enabled_collector_count(self) -> int:
        """Number of enabled collectors."""
        return len(self._collectors)

    @property
    def enabled_collector_names(self) -> List[str]:
        """Names of all enabled collectors."""
        return list(self._collectors.keys())

    @property
    def mqtt_subscriber(self) -> Optional[MQTTSubscriber]:
        """The MQTT subscriber instance, or None if not configured."""
        return self._mqtt_subscriber

    def clear_all_caches(self) -> None:
        for collector in self._collectors.values():
            collector.clear_cache()
        with self._data_lock:
            self._cached_overlay = {}

    def shutdown(self) -> None:
        """Stop MQTT subscriber and release resources."""
        if self._mqtt_subscriber:
            self._mqtt_subscriber.stop()
            self._mqtt_subscriber = None
        self._cached_overlay = {}
        logger.info("DataAggregator shut down")
