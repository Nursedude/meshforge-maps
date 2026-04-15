"""
MeshForge Maps - Data Aggregator

Merges GeoJSON FeatureCollections from all enabled collectors
into a single unified collection with deduplication.
"""

import gc
import gzip
import hashlib
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .aredn_collector import AREDNCollector
from .base import (
    deduplicate_features, make_feature_collection, make_geometry_feature,
    make_link_feature, normalize_bboxes,
)
from .hamclock_collector import HamClockCollector
from .meshtastic_collector import MeshtasticCollector
from .meshcore_collector import MeshCoreCollector
from .mqtt_subscriber import MQTTNodeStore, MQTTSubscriber
from .noaa_alert_collector import NOAAAlertCollector
from .reticulum_collector import ReticulumCollector
from ..utils.config import REGION_PRESETS
from ..utils.event_bus import EventBus
from ..utils.perf_monitor import PerfMonitor

logger = logging.getLogger(__name__)

# Default retry count for collectors (before cache fallback)
DEFAULT_COLLECTOR_RETRIES = 2


def _mqtt_store_cap(config) -> int:
    """Tiered MQTTNodeStore capacity based on deployment profile."""
    if getattr(config, "is_lite", False):
        return 1000
    if getattr(config, "is_medium", False):
        return 5000
    return 10000


def _resolve_broker_specs(config) -> List[Dict[str, Any]]:
    """Build the list of MQTT broker specs from config.

    If `mqtt_brokers` is populated, each entry is used as-is (with safe defaults).
    Otherwise a single spec is synthesized from the legacy scalar mqtt_* keys so
    existing single-broker configs keep working.
    """
    brokers_raw = config.get("mqtt_brokers") or []
    specs: List[Dict[str, Any]] = []
    if brokers_raw:
        for idx, entry in enumerate(brokers_raw):
            if not isinstance(entry, dict) or not entry.get("broker"):
                logger.warning("Skipping invalid mqtt_brokers entry at index %d", idx)
                continue
            specs.append({
                "broker": entry["broker"],
                "port": int(entry.get("port", 1883)),
                "topic": entry.get("topic", "msh/#"),
                "username": entry.get("username"),
                "password": entry.get("password"),
                "use_tls": bool(entry.get("use_tls", False)),
                "label": entry.get("label"),
            })
    if not specs:
        specs.append({
            "broker": config.get("mqtt_broker", "mqtt.meshtastic.org"),
            "port": int(config.get("mqtt_port", 1883)),
            "topic": config.get("mqtt_topic", "msh/#"),
            "username": config.get("mqtt_username", "meshdev"),
            "password": config.get("mqtt_password", "large4cats"),
            "use_tls": bool(config.get("mqtt_use_tls", False)),
            "label": "primary",
        })
    return specs


class DataAggregator:
    """Aggregates data from all enabled collectors into unified GeoJSON."""

    def __init__(self, config):
        self._config = config
        # Use get_effective() for lite-mode-aware values when config is MapsConfig
        _get = getattr(config, "get_effective", None) or config.get
        cache_ttl = _get("cache_ttl_minutes", 15) * 60
        self._collectors = {}
        self._data_lock = threading.Lock()
        self._cached_overlay: Dict[str, Any] = {}
        self._last_collect_time: float = 0
        self._last_collect_counts: Dict[str, int] = {}
        self._cached_result: Optional[Dict[str, Any]] = None
        self._cached_result_time: float = 0
        self._RESULT_CACHE_TTL = 2.0  # seconds — dedup rapid requests
        # Pre-serialized JSON cache (avoids json.dumps + gzip on every request)
        self._cached_json: Optional[bytes] = None
        self._cached_json_gzip: Optional[bytes] = None
        self._cached_json_etag: Optional[str] = None
        self._node_history = None  # Optional NodeHistoryDB for analytics recording
        self._obs_thread: Optional[threading.Thread] = None

        # Event bus for decoupled real-time communication
        self._event_bus = EventBus()

        # Performance monitor for collection timing
        self._perf_monitor = PerfMonitor()

        retries = DEFAULT_COLLECTOR_RETRIES

        # Log deployment profile
        is_lite = getattr(config, "is_lite", False)
        if is_lite:
            logger.info("Lite deployment profile active (reduced collectors, longer cache)")

        # Region scope: clip big global collectors (meshcore, AREDN worldmap) to
        # the configured region preset so lite-mode Pis can still include them.
        preset_key = config.get("region_preset")
        preset_data = REGION_PRESETS.get(preset_key, {}) if preset_key else {}
        region_bboxes = normalize_bboxes(preset_data.get("bbox"))
        region_polygons = preset_data.get("polygons") or None
        # Safety: in lite mode with no region scope, keep meshcore + aredn_worldmap
        # off — fetching 34K global nodes on a Pi is the condition the 0.7.1 fix
        # was created to prevent.
        lite_unscoped = is_lite and not region_bboxes and not region_polygons
        if region_bboxes or region_polygons:
            logger.info(
                "Region scope '%s' active: %d bbox(es), %d polygon(s) applied to meshcore/aredn_worldmap/reticulum",
                preset_key, len(region_bboxes or []), len(region_polygons or []),
            )
        elif lite_unscoped:
            logger.info("Lite + world/no region: meshcore and AREDN worldmap disabled (safety)")

        # Initialize live MQTT subscribers for Meshtastic.
        # Multiple brokers can feed the same MQTTNodeStore concurrently; the
        # first entry is the "primary" used for topology/stats reporting.
        self._mqtt_subscriber: Optional[MQTTSubscriber] = None
        self._mqtt_secondary: List[MQTTSubscriber] = []
        mqtt_store: Optional[MQTTNodeStore] = None
        if config.get("enable_meshtastic", True):
            node_store = MQTTNodeStore(max_nodes=_mqtt_store_cap(config))
            broker_specs = _resolve_broker_specs(config)
            for idx, spec in enumerate(broker_specs):
                sub = MQTTSubscriber(
                    broker=spec["broker"],
                    port=spec["port"],
                    topic=spec["topic"],
                    username=spec.get("username"),
                    password=spec.get("password"),
                    tls=spec.get("use_tls", False),
                    node_store=node_store,
                    event_bus=self._event_bus if idx == 0 else None,
                )
                if not sub.available:
                    logger.warning("MQTT: paho-mqtt unavailable; skipping broker %s", spec["broker"])
                    continue
                sub.start()
                if self._mqtt_subscriber is None:
                    self._mqtt_subscriber = sub
                else:
                    self._mqtt_secondary.append(sub)
                logger.info("MQTT broker started: %s:%d (%s)",
                            spec["broker"], spec["port"], spec.get("label") or "primary" if idx == 0 else spec.get("label") or f"broker{idx}")
            if self._mqtt_subscriber is not None:
                mqtt_store = self._mqtt_subscriber.store

        if config.get("enable_meshtastic", True):
            self._collectors["meshtastic"] = MeshtasticCollector(
                meshtasticd_host=config.get("meshtasticd_host", "localhost"),
                meshtasticd_port=config.get("meshtasticd_port", 4403),
                cache_ttl_seconds=cache_ttl,
                max_retries=retries,
                mqtt_store=mqtt_store,
                source_mode=config.get("meshtastic_source", "auto"),
            )

        if config.get("enable_reticulum", True):
            self._collectors["reticulum"] = ReticulumCollector(
                rch_host=config.get("rch_host", "localhost"),
                rch_port=config.get("rch_port", 8000),
                rch_api_key=config.get("rch_api_key"),
                enable_rmap_public=config.get("enable_rmap_public", True),
                cache_ttl_seconds=cache_ttl,
                max_retries=retries,
                region_bboxes=region_bboxes,
                region_polygons=region_polygons,
            )

        if config.get("enable_hamclock", True):
            self._collectors["hamclock"] = HamClockCollector(
                hamclock_host=config.get("hamclock_host", "localhost"),
                hamclock_port=config.get("hamclock_port", 8080),
                openhamclock_port=config.get("openhamclock_port", 3000),
                cache_ttl_seconds=cache_ttl,
                max_retries=retries,
            )

        if config.get("enable_aredn", True):
            aredn_worldmap = _get("enable_aredn_worldmap", True) and not lite_unscoped
            self._collectors["aredn"] = AREDNCollector(
                node_targets=config.get("aredn_node_targets"),
                enable_worldmap=aredn_worldmap,
                cache_ttl_seconds=cache_ttl,
                max_retries=retries,
                region_bboxes=region_bboxes,
                region_polygons=region_polygons,
            )

        if _get("enable_meshcore", True) and not lite_unscoped:
            self._collectors["meshcore"] = MeshCoreCollector(
                enable_map=_get("enable_meshcore_map", True),
                cache_ttl_seconds=max(cache_ttl, 1800),  # 30min min for large API
                max_retries=retries,
                region_bboxes=region_bboxes,
                region_polygons=region_polygons,
            )

        # NOAA weather alerts (polygon overlay — not included in collect_all)
        if config.get("enable_noaa_alerts", True):
            self._collectors["noaa_alerts"] = NOAAAlertCollector(
                area=config.get("noaa_alerts_area"),
                severity_filter=config.get("noaa_alerts_severity"),
                cache_ttl_seconds=min(cache_ttl, 300),  # Cap at 5 min for alerts
                max_retries=retries,
            )

    # Collectors that return polygon/overlay data — excluded from collect_all()
    # because their features are not mesh node points.
    _OVERLAY_ONLY_COLLECTORS = {"noaa_alerts"}

    def set_node_history(self, db) -> None:
        """Set optional NodeHistoryDB for recording observations from all sources."""
        self._node_history = db

    def _record_observations(self, features: List[Dict[str, Any]]) -> None:
        """Batch-record observations from deduplicated features into node history."""
        if not self._node_history:
            return
        if not getattr(self._node_history, '_conn', None):
            logger.warning("Observation recording skipped: DB connection unavailable")
            return
        obs_list = []
        for f in features:
            geom = f.get("geometry", {})
            coords = geom.get("coordinates")
            if not coords or len(coords) < 2:
                continue
            props = f.get("properties", {})
            node_id = props.get("id")
            if not node_id:
                continue
            obs_list.append({
                "node_id": node_id,
                "lat": coords[1],
                "lon": coords[0],
                "network": props.get("network", ""),
                "snr": props.get("snr"),
                "battery": props.get("battery"),
                "altitude": props.get("altitude"),
                "name": props.get("name", ""),
            })
        if obs_list:
            try:
                count = self._node_history.record_observations_batch(obs_list)
                if count:
                    logger.info("Recorded %d/%d observations to history", count, len(obs_list))
            except Exception as e:
                logger.warning("Observation recording failed: %s", e)

    def collect_all(self) -> Dict[str, Any]:
        """Collect from all enabled sources and merge into one FeatureCollection.

        Results are cached for 2 seconds to avoid redundant collection cycles
        when multiple clients request data simultaneously.
        """
        now = time.monotonic()
        with self._data_lock:
            if (self._cached_result is not None
                    and now - self._cached_result_time < self._RESULT_CACHE_TTL):
                return self._cached_result

        per_source_features: List[List[Dict[str, Any]]] = []
        source_counts: Dict[str, int] = {}
        overlay_data: Dict[str, Any] = {}

        # Pre-populate so all enabled sources appear in output even on failure
        for name in self._collectors:
            if name not in self._OVERLAY_ONLY_COLLECTORS:
                source_counts[name] = 0

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
                        src_ctx.from_cache = collector.is_cache_hit(fc)

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

        # Record observations in background thread (non-blocking)
        # SQLite writes on Pi SD card take ~80s for 42K nodes — don't block result caching
        if self._node_history and all_features:
            can_spawn = True
            if self._obs_thread and self._obs_thread.is_alive():
                logger.debug("Waiting for previous observation thread")
                self._obs_thread.join(timeout=120)
                if self._obs_thread.is_alive():
                    logger.warning(
                        "Observation thread still running after 120s — "
                        "skipping this cycle"
                    )
                    can_spawn = False
            if can_spawn:
                self._obs_thread = threading.Thread(
                    target=self._record_observations,
                    args=(list(all_features),),
                    daemon=True,
                )
                self._obs_thread.start()

        # Cache overlay data so /api/overlay doesn't trigger a full re-collect
        with self._data_lock:
            self._cached_overlay = overlay_data
            self._last_collect_time = time.time()
            self._last_collect_counts = dict(source_counts)
            # Cache the full result for dedup (cleared after _RESULT_CACHE_TTL)
            self._cached_result_time = time.monotonic()

        result = make_feature_collection(all_features, "aggregated")
        result["properties"]["sources"] = source_counts
        result["properties"]["total_nodes"] = len(all_features)
        result["properties"]["enabled_sources"] = list(self._collectors.keys())
        result["properties"]["overlay_data"] = overlay_data

        with self._data_lock:
            self._cached_result = result
            # Pre-serialize JSON + gzip so HTTP handler avoids per-request cost
            try:
                raw = json.dumps(result, default=str).encode("utf-8")
                self._cached_json = raw
                self._cached_json_gzip = gzip.compress(raw)
                self._cached_json_etag = hashlib.md5(raw).hexdigest()
            except Exception as e:
                logger.debug("JSON pre-serialization failed: %s", e)
                self._cached_json = None
                self._cached_json_gzip = None
                self._cached_json_etag = None

        logger.info(
            "Aggregated %d nodes from %d sources: %s",
            len(all_features),
            len(self._collectors),
            source_counts,
        )
        # Explicit GC trims the intermediate feature lists and dicts that pile up
        # between cycles. Small cost (~10-30 ms on a Pi), meaningful RSS relief.
        gc.collect()
        return result

    def get_cached_result(self) -> Optional[Dict[str, Any]]:
        """Return cached collect_all() result without triggering collection."""
        with self._data_lock:
            return self._cached_result

    def get_cached_json(self) -> Optional[Tuple[bytes, bytes, str]]:
        """Return pre-serialized (json_bytes, gzip_bytes, etag) or None."""
        with self._data_lock:
            if self._cached_json is not None:
                return (self._cached_json, self._cached_json_gzip,
                        self._cached_json_etag)
            return None

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
                # Include partially-resolved links as metadata-only features
                if "source_lat" not in link or "target_lat" not in link:
                    partial_feature = make_geometry_feature(
                        None,
                        source=link.get("source", ""),
                        target=link.get("target", ""),
                        network="aredn",
                        link_type=link.get("link_type", ""),
                        quality=link.get("quality"),
                        partial=True,
                    )
                    result["features"].append(partial_feature)
                    continue
                snr = link.get("snr")
                quality_label, color = _classify_snr(snr)
                feature = make_link_feature(
                    link.get("source", ""), link.get("target", ""),
                    (link["source_lon"], link["source_lat"]),
                    (link["target_lon"], link["target_lat"]),
                    snr=snr, quality=quality_label, color=color,
                    network="aredn", link_type=link.get("link_type", ""),
                    aredn_quality=link.get("quality"),
                )
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
        """Primary MQTT subscriber (first broker) used for topology/stats."""
        return self._mqtt_subscriber

    @property
    def mqtt_subscribers(self) -> List[MQTTSubscriber]:
        """All active MQTT subscribers (primary + secondary)."""
        subs: List[MQTTSubscriber] = []
        if self._mqtt_subscriber is not None:
            subs.append(self._mqtt_subscriber)
        subs.extend(self._mqtt_secondary)
        return subs

    def mqtt_broker_status(self) -> List[Dict[str, Any]]:
        """Per-broker connection status for /api/status."""
        out = []
        for sub in self.mqtt_subscribers:
            out.append({
                "broker": getattr(sub, "_broker", None),
                "port": getattr(sub, "_port", None),
                "topic": getattr(sub, "_topic", None),
                "connected": sub._connected.is_set() if hasattr(sub, "_connected") else False,
            })
        return out

    def restart_mqtt(self, config: Dict[str, Any]) -> bool:
        """Restart all MQTT subscribers with updated config. Returns True on success."""
        for sub in self.mqtt_subscribers:
            sub.stop()
        self._mqtt_subscriber = None
        self._mqtt_secondary = []

        if not config.get("enable_meshtastic", True):
            logger.info("MQTT restart skipped: meshtastic disabled")
            return False

        node_store = MQTTNodeStore(max_nodes=_mqtt_store_cap(config))
        for idx, spec in enumerate(_resolve_broker_specs(config)):
            sub = MQTTSubscriber(
                broker=spec["broker"],
                port=spec["port"],
                topic=spec["topic"],
                username=spec.get("username"),
                password=spec.get("password"),
                tls=spec.get("use_tls", False),
                node_store=node_store,
                event_bus=self._event_bus if idx == 0 else None,
            )
            if not sub.available:
                logger.warning("MQTT restart: paho-mqtt unavailable")
                continue
            sub.start()
            if self._mqtt_subscriber is None:
                self._mqtt_subscriber = sub
            else:
                self._mqtt_secondary.append(sub)
            logger.info("MQTT broker restarted: %s:%d", spec["broker"], spec["port"])

        if self._mqtt_subscriber is None:
            return False

        meshtastic = self._collectors.get("meshtastic")
        if meshtastic and hasattr(meshtastic, "_mqtt_store"):
            meshtastic._mqtt_store = self._mqtt_subscriber.store
        return True

    def clear_all_caches(self) -> None:
        for collector in self._collectors.values():
            collector.clear_cache()
        with self._data_lock:
            self._cached_overlay = {}
            self._cached_result = None

    def shutdown(self) -> None:
        """Stop MQTT subscribers, reset event bus, and release resources."""
        for sub in self.mqtt_subscribers:
            sub.stop()
        self._mqtt_subscriber = None
        self._mqtt_secondary = []
        if self._obs_thread and self._obs_thread.is_alive():
            self._obs_thread.join(timeout=30)
        self._event_bus.reset()
        self._cached_overlay = {}
        logger.info("DataAggregator shut down")
