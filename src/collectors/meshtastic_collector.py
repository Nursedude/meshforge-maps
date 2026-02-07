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

from .base import BaseCollector, make_feature, make_feature_collection

logger = logging.getLogger(__name__)

# Local meshtasticd HTTP API
MESHTASTICD_API = "http://localhost:4403"
NODES_ENDPOINT = "/api/v1/nodes"

# Meshforge MQTT cache location
MQTT_CACHE_PATH = Path.home() / ".local" / "share" / "meshforge" / "mqtt_nodes.json"


class MeshtasticCollector(BaseCollector):
    """Collects Meshtastic node data from local daemon, live MQTT, and MQTT cache."""

    source_name = "meshtastic"

    def __init__(
        self,
        meshtasticd_host: str = "localhost",
        meshtasticd_port: int = 4403,
        cache_ttl_seconds: int = 900,
        mqtt_store: Optional[Any] = None,
    ):
        super().__init__(cache_ttl_seconds)
        self._api_base = f"http://{meshtasticd_host}:{meshtasticd_port}"
        self._mqtt_store = mqtt_store  # MQTTNodeStore instance from mqtt_subscriber

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
        """Fetch from local meshtasticd HTTP API."""
        features = []
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

        if lat is None or lon is None:
            return None

        # meshtasticd may return integer lat/lon (latitudeI = lat * 1e7)
        if isinstance(lat, int) and abs(lat) > 900:
            lat = lat / 1e7
        if isinstance(lon, int) and abs(lon) > 1800:
            lon = lon / 1e7

        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None

        user = node.get("user", {})
        node_id = user.get("id", node.get("num", ""))
        name = user.get("longName", user.get("shortName", str(node_id)))
        hardware = user.get("hwModel", "")
        role = user.get("role", "")

        device_metrics = node.get("deviceMetrics", {})
        battery = device_metrics.get("batteryLevel")

        snr = node.get("snr")
        last_heard = node.get("lastHeard")
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
            snr=snr,
            is_online=is_online,
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
        lat = node.get("latitude")
        lon = node.get("longitude")
        if lat is None or lon is None:
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None

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
            snr=node.get("snr"),
            is_online=node.get("is_online"),
            last_seen=node.get("last_seen"),
        )
