"""
MeshForge Maps - Data Aggregator

Merges GeoJSON FeatureCollections from all enabled collectors
into a single unified collection with deduplication.
"""

import logging
import time
from typing import Any, Dict, List, Optional

from .aredn_collector import AREDNCollector
from .base import make_feature_collection
from .hamclock_collector import HamClockCollector
from .meshtastic_collector import MeshtasticCollector
from .mqtt_subscriber import MQTTNodeStore, MQTTSubscriber
from .reticulum_collector import ReticulumCollector

logger = logging.getLogger(__name__)


class DataAggregator:
    """Aggregates data from all enabled collectors into unified GeoJSON."""

    def __init__(self, config: Dict[str, Any]):
        self._config = config
        cache_ttl = config.get("cache_ttl_minutes", 15) * 60
        self._collectors = {}

        # Initialize live MQTT subscriber for Meshtastic
        self._mqtt_subscriber: Optional[MQTTSubscriber] = None
        mqtt_store: Optional[MQTTNodeStore] = None
        if config.get("enable_meshtastic", True):
            self._mqtt_subscriber = MQTTSubscriber()
            if self._mqtt_subscriber.available:
                self._mqtt_subscriber.start()
                mqtt_store = self._mqtt_subscriber.store
            else:
                self._mqtt_subscriber = None

        if config.get("enable_meshtastic", True):
            self._collectors["meshtastic"] = MeshtasticCollector(
                cache_ttl_seconds=cache_ttl,
                mqtt_store=mqtt_store,
            )

        if config.get("enable_reticulum", True):
            self._collectors["reticulum"] = ReticulumCollector(
                cache_ttl_seconds=cache_ttl
            )

        if config.get("enable_hamclock", True):
            self._collectors["hamclock"] = HamClockCollector(
                cache_ttl_seconds=cache_ttl
            )

        if config.get("enable_aredn", True):
            self._collectors["aredn"] = AREDNCollector(
                cache_ttl_seconds=cache_ttl
            )

    def collect_all(self) -> Dict[str, Any]:
        """Collect from all enabled sources and merge into one FeatureCollection."""
        all_features: List[Dict[str, Any]] = []
        seen_ids: set = set()
        source_counts: Dict[str, int] = {}
        overlay_data: Dict[str, Any] = {}

        for name, collector in self._collectors.items():
            try:
                fc = collector.collect()
                features = fc.get("features", [])
                source_counts[name] = len(features)

                for feature in features:
                    fid = feature.get("properties", {}).get("id")
                    if fid and fid not in seen_ids:
                        seen_ids.add(fid)
                        all_features.append(feature)
                    elif not fid:
                        all_features.append(feature)

                # Capture overlay data (space weather, terminator, etc.)
                fc_props = fc.get("properties", {})
                for key in ("space_weather", "solar_terminator", "hamclock"):
                    if key in fc_props:
                        overlay_data[key] = fc_props[key]

            except Exception as e:
                logger.error("Collector %s failed: %s", name, e)
                source_counts[name] = 0

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
        """Get topology link data from MQTT subscriber."""
        if self._mqtt_subscriber:
            return self._mqtt_subscriber.store.get_topology_links()
        return []

    def clear_all_caches(self) -> None:
        for collector in self._collectors.values():
            collector.clear_cache()
