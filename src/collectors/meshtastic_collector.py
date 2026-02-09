"""
MeshForge Maps - Meshtastic Data Collector

Collects node data from Meshtastic public MQTT broker and local meshtasticd.
Data sources (in priority order):
  1. Local meshtasticd HTTP API (localhost:4403/api/v1/nodes)
  2. MQTT subscriber cache (mqtt_nodes.json)
  3. Public MQTT broker at mqtt.meshtastic.org (topic: msh/#)

Meshtastic nodes broadcast: POSITION_APP, NODEINFO_APP, NEIGHBORINFO_APP,
TELEMETRY_APP, TRACEROUTE_APP via protobuf over MQTT.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import BaseCollector, make_feature, make_feature_collection, validate_coordinates
from ..utils.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

# Local meshtasticd HTTP API
MESHTASTICD_API = "http://localhost:4403"
NODES_ENDPOINT = "/api/v1/nodes"

# Meshforge MQTT cache location
MQTT_CACHE_PATH = Path.home() / ".local" / "share" / "meshforge" / "mqtt_nodes.json"


class MeshtasticCollector(BaseCollector):
    """Collects Meshtastic node data from local daemon, live MQTT, and MQTT cache.

    Uses ConnectionManager to prevent TCP contention with MeshForge core's
    gateway when accessing meshtasticd's single-client HTTP API.
    """

    source_name = "meshtastic"

    def __init__(
        self,
        meshtasticd_host: str = "localhost",
        meshtasticd_port: int = 4403,
        cache_ttl_seconds: int = 900,
        mqtt_store: Optional[Any] = None,
        connection_timeout: float = 5.0,
    ):
        super().__init__(cache_ttl_seconds)
        self._api_base = f"http://{meshtasticd_host}:{meshtasticd_port}"
        self._mqtt_store = mqtt_store  # MQTTNodeStore instance from mqtt_subscriber
        self._conn_mgr = ConnectionManager.get_instance(meshtasticd_host, meshtasticd_port)
        self._connection_timeout = connection_timeout

    def _fetch(self) -> Dict[str, Any]:
        features: List[Dict[str, Any]] = []
        seen_ids: set = set()

        # Source 1: Local meshtasticd HTTP API
        api_nodes = self._fetch_from_api()
        for f in api_nodes:
            fid = f["properties"].get("id")
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                features.append(f)

        # Source 2: Live MQTT subscriber (real-time nodes)
        live_nodes = self._fetch_from_live_mqtt()
        for f in live_nodes:
            fid = f["properties"].get("id")
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                features.append(f)

        # Source 3: MQTT subscriber cache file (fallback)
        mqtt_nodes = self._fetch_from_mqtt_cache()
        for f in mqtt_nodes:
            fid = f["properties"].get("id")
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                features.append(f)

        return make_feature_collection(features, self.source_name)

    def _fetch_from_api(self) -> List[Dict[str, Any]]:
        """Fetch from local meshtasticd HTTP API.

        Uses ConnectionManager to acquire exclusive access before connecting,
        preventing TCP contention with MeshForge core's gateway.
        """
        features = []
        with self._conn_mgr.acquire(
            timeout=self._connection_timeout, holder="maps_collector"
        ) as acquired:
            if not acquired:
                logger.debug(
                    "meshtasticd connection held by '%s', skipping API fetch",
                    self._conn_mgr.holder,
                )
                return features
            try:
                url = f"{self._api_base}{NODES_ENDPOINT}"
                req = Request(url, headers={"Accept": "application/json"})
                with urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode())

                nodes = data if isinstance(data, list) else data.get("nodes", [])
                for node in nodes:
                    feature = self._parse_api_node(node)
                    if feature:
                        features.append(feature)
                logger.debug("meshtasticd API returned %d nodes", len(features))
            except (URLError, OSError, json.JSONDecodeError) as e:
                logger.debug("meshtasticd API unavailable: %s", e)
        return features

    def _parse_api_node(self, node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse a node from the meshtasticd API into a GeoJSON feature."""
        position = node.get("position", {})
        lat = position.get("latitude") or position.get("latitudeI")
        lon = position.get("longitude") or position.get("longitudeI")

        coords = validate_coordinates(lat, lon, convert_int=True)
        if coords is None:
            return None
        lat, lon = coords

        user = node.get("user", {})
        node_id = user.get("id", node.get("num", ""))
        name = user.get("longName", user.get("shortName", str(node_id)))
        hardware = user.get("hwModel", "")
        role = user.get("role", "")

        device_metrics = node.get("deviceMetrics", {}) or {}
        battery = device_metrics.get("batteryLevel")
        voltage = device_metrics.get("voltage")
        channel_util = device_metrics.get("channelUtilization")
        air_util_tx = device_metrics.get("airUtilTx")

        snr = node.get("snr")
        last_heard = node.get("lastHeard")
        hops_away = node.get("hopsAway")
        via_mqtt = node.get("viaMqtt")
        is_online = None
        if last_heard:
            age_seconds = time.time() - last_heard
            is_online = age_seconds < 900  # 15 min threshold

        return make_feature(
            node_id=str(node_id),
            lat=lat,
            lon=lon,
            network="meshtastic",
            name=name,
            node_type="meshtastic_node",
            hardware=hardware,
            role=role,
            battery=battery,
            voltage=voltage,
            snr=snr,
            is_online=is_online,
            is_local=hops_away == 0 if hops_away is not None else None,
            is_gateway=role in ("ROUTER", "ROUTER_CLIENT") if role else None,
            is_relay=role in ("ROUTER", "ROUTER_CLIENT", "REPEATER") if role else None,
            hops_away=hops_away,
            via_mqtt=via_mqtt,
            channel_util=channel_util,
            air_util_tx=air_util_tx,
            last_seen=last_heard,
            altitude=position.get("altitude"),
        )

    def _fetch_from_live_mqtt(self) -> List[Dict[str, Any]]:
        """Get nodes from live MQTT subscriber's in-memory store."""
        if not self._mqtt_store:
            return []
        try:
            nodes = self._mqtt_store.get_all_nodes()
            features = []
            for node in nodes:
                feature = self._parse_mqtt_node(node.get("id", ""), node)
                if feature:
                    features.append(feature)
            logger.debug("Live MQTT returned %d meshtastic nodes", len(features))
            return features
        except Exception as e:
            logger.debug("Live MQTT fetch failed: %s", e)
            return []

    def _fetch_from_mqtt_cache(self) -> List[Dict[str, Any]]:
        """Read cached MQTT node data from meshforge's mqtt_nodes.json."""
        features = []
        if not MQTT_CACHE_PATH.exists():
            return features
        try:
            with open(MQTT_CACHE_PATH, "r") as f:
                data = json.load(f)

            # mqtt_nodes.json may be a GeoJSON FeatureCollection or a dict of nodes
            if data.get("type") == "FeatureCollection":
                for f_item in data.get("features", []):
                    props = f_item.get("properties", {})
                    if props.get("network") == "meshtastic":
                        features.append(f_item)
            elif isinstance(data, dict):
                for node_id, node_data in data.items():
                    feature = self._parse_mqtt_node(node_id, node_data)
                    if feature:
                        features.append(feature)

            logger.debug("MQTT cache returned %d meshtastic nodes", len(features))
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("MQTT cache read failed: %s", e)
        return features

    def _parse_mqtt_node(
        self, node_id: str, node: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Parse a node from MQTT cache format."""
        coords = validate_coordinates(node.get("latitude"), node.get("longitude"))
        if coords is None:
            return None
        lat, lon = coords

        return make_feature(
            node_id=node_id,
            lat=lat,
            lon=lon,
            network="meshtastic",
            name=node.get("name", node_id),
            node_type="meshtastic_node",
            hardware=node.get("hardware", ""),
            role=node.get("role", ""),
            battery=node.get("battery"),
            voltage=node.get("voltage"),
            snr=node.get("snr"),
            is_online=node.get("is_online"),
            last_seen=node.get("last_seen"),
            temperature=node.get("temperature"),
            humidity=node.get("humidity"),
            pressure=node.get("pressure"),
            channel_util=node.get("channel_util"),
            air_util_tx=node.get("air_util_tx"),
            altitude=node.get("altitude"),
            # Air quality metrics
            iaq=node.get("iaq"),
            pm25_standard=node.get("pm25_standard"),
            pm100_standard=node.get("pm100_standard"),
            co2=node.get("co2"),
            pm_voc_idx=node.get("pm_voc_idx"),
            pm_nox_idx=node.get("pm_nox_idx"),
            # Health metrics
            heart_bpm=node.get("heart_bpm"),
            spo2=node.get("spo2"),
            body_temperature=node.get("body_temperature"),
        )
