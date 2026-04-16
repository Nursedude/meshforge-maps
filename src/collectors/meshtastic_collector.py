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
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import MESHFORGE_DATA_DIR, BaseCollector, bounded_read, is_node_online, make_feature, make_feature_collection, validate_coordinates
from ..utils.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

# Local meshtasticd HTTP API
MESHTASTICD_API = "http://localhost:4403"
NODES_ENDPOINT = "/api/v1/nodes"

# External map data source (aggregated from public MQTT)
MESHMAP_URL = "https://meshmap.net/nodes.json"

# Meshforge MQTT cache location
MQTT_CACHE_PATH = MESHFORGE_DATA_DIR / "mqtt_nodes.json"


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
        max_retries: int = 0,
        mqtt_store: Optional[Any] = None,
        connection_timeout: float = 5.0,
        source_mode: str = "auto",
    ):
        super().__init__(cache_ttl_seconds, max_retries=max_retries)
        self._api_base = f"http://{meshtasticd_host}:{meshtasticd_port}"
        self._mqtt_store = mqtt_store  # MQTTNodeStore instance from mqtt_subscriber
        self._conn_mgr = ConnectionManager.get_instance(meshtasticd_host, meshtasticd_port)
        self._connection_timeout = connection_timeout
        # "auto" = API → MQTT → cache; "mqtt_only" = skip API; "local_only" = API only
        self._source_mode = source_mode

    def _fetch(self) -> Dict[str, Any]:
        features: List[Dict[str, Any]] = []
        seen_ids: set = set()

        # Source 1: Local meshtasticd HTTP API (skipped in mqtt_only mode)
        if self._source_mode != "mqtt_only":
            api_nodes = self._fetch_from_api()
            for f in api_nodes:
                fid = f["properties"].get("id")
                if fid and fid not in seen_ids:
                    seen_ids.add(fid)
                    features.append(f)

        # Source 2: Live MQTT subscriber (real-time nodes) (skipped in local_only mode)
        if self._source_mode != "local_only":
            live_nodes = self._fetch_from_live_mqtt()
            for f in live_nodes:
                fid = f["properties"].get("id")
                if fid and fid not in seen_ids:
                    seen_ids.add(fid)
                    features.append(f)

        # Source 3: MQTT subscriber cache file (skipped in local_only mode)
        if self._source_mode != "local_only":
            mqtt_nodes = self._fetch_from_mqtt_cache()
            for f in mqtt_nodes:
                fid = f["properties"].get("id")
                if fid and fid not in seen_ids:
                    seen_ids.add(fid)
                    features.append(f)

        # Source 4: meshmap.net public API (skipped in local_only mode)
        if self._source_mode != "local_only":
            meshmap_nodes = self._fetch_from_meshmap()
            for f in meshmap_nodes:
                fid = f["properties"].get("id")
                if fid and fid not in seen_ids:
                    seen_ids.add(fid)
                    features.append(f)

        return make_feature_collection(features, self.source_name)

    def _fetch_from_api(self) -> List[Dict[str, Any]]:
        """Fetch from local meshtasticd HTTP API.

        Uses ConnectionManager to acquire exclusive access before connecting,
        preventing TCP contention with MeshForge core's gateway.
        Retries once on transient connection errors (ConnectionRefusedError,
        timeout) before falling back to cache sources.
        """
        features = []
        # HTTP timeout must be shorter than the lock timeout so the lock
        # is never released while a request is still in flight.
        http_timeout = max(1.0, self._connection_timeout - 1.0)
        for lock_attempt in range(2):
            with self._conn_mgr.acquire(
                timeout=self._connection_timeout, holder="maps_collector"
            ) as acquired:
                if not acquired:
                    if lock_attempt == 0:
                        logger.debug(
                            "meshtasticd lock contention, retrying in 1s",
                        )
                        time.sleep(1.0)
                        continue
                    logger.debug(
                        "meshtasticd connection held by '%s', skipping API fetch",
                        self._conn_mgr.holder,
                    )
                    return features

                url = f"{self._api_base}{NODES_ENDPOINT}"
                last_err: Optional[Exception] = None
                for attempt in range(2):
                    try:
                        req = Request(url, headers={"Accept": "application/json"})
                        with urlopen(req, timeout=http_timeout) as resp:
                            data = json.loads(bounded_read(resp).decode())

                        nodes = data if isinstance(data, list) else data.get("nodes", [])
                        for node in nodes:
                            feature = self._parse_api_node(node)
                            if feature:
                                features.append(feature)
                        logger.debug("meshtasticd API returned %d nodes", len(features))
                        last_err = None
                        break
                    except (URLError, OSError, json.JSONDecodeError) as e:
                        last_err = e
                        if attempt == 0 and isinstance(e, (URLError, OSError)):
                            logger.debug(
                                "meshtasticd API attempt %d failed: %s, retrying",
                                attempt + 1, e,
                            )
                            time.sleep(0.5)
                            continue
                        break

                if last_err is not None:
                    logger.debug("meshtasticd API unavailable: %s", last_err)
                return features
        return features

    def _parse_api_node(self, node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse a node from the meshtasticd API into a GeoJSON feature."""
        position = node.get("position", {})
        lat = position.get("latitude")
        if lat is None:
            lat = position.get("latitudeI")
        lon = position.get("longitude")
        if lon is None:
            lon = position.get("longitudeI")

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
        is_online = is_node_online(last_heard, "meshtastic")

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
            is_online=is_node_online(node.get("last_seen"), "mqtt"),
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
            # Weather / environmental (expanded)
            wind_direction=node.get("wind_direction"),
            wind_speed=node.get("wind_speed"),
            wind_gust=node.get("wind_gust"),
            rainfall_1h=node.get("rainfall_1h"),
            rainfall_24h=node.get("rainfall_24h"),
            soil_moisture=node.get("soil_moisture"),
            soil_temperature=node.get("soil_temperature"),
            lux=node.get("lux"),
            uv_lux=node.get("uv_lux"),
            radiation=node.get("radiation"),
            # Power metrics
            power_ch1_voltage=node.get("power_ch1_voltage"),
            power_ch1_current=node.get("power_ch1_current"),
            power_ch2_voltage=node.get("power_ch2_voltage"),
            power_ch2_current=node.get("power_ch2_current"),
            # Device stats
            noise_floor=node.get("noise_floor"),
            num_online_nodes=node.get("num_online_nodes"),
            # Map report fields
            firmware_version=node.get("firmware_version"),
            region=node.get("region"),
            modem_preset=node.get("modem_preset"),
        )

    def _fetch_from_meshmap(self) -> List[Dict[str, Any]]:
        """Fetch aggregated Meshtastic nodes from meshmap.net public API."""
        features = []
        try:
            req = Request(
                MESHMAP_URL,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "MeshForge/1.0",
                },
            )
            with urlopen(req, timeout=15) as resp:
                data = json.loads(bounded_read(resp).decode())

            for num_id, node in data.items():
                feature = self._parse_meshmap_node(num_id, node)
                if feature:
                    features.append(feature)
            if features:
                logger.debug("meshmap.net returned %d meshtastic nodes", len(features))
        except (URLError, OSError, json.JSONDecodeError, ValueError) as e:
            logger.debug("meshmap.net unavailable: %s", e)
        return features

    def _parse_meshmap_node(
        self, num_id: str, node: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Parse a node from meshmap.net nodes.json format."""
        coords = validate_coordinates(
            node.get("latitude"), node.get("longitude"), convert_int=True,
        )
        if coords is None:
            return None
        lat, lon = coords

        # Convert numeric ID to Meshtastic hex format: !3d2a114e
        try:
            hex_id = f"!{int(num_id):08x}"
        except (ValueError, TypeError):
            return None

        last_seen = node.get("lastMapReport")
        return make_feature(
            node_id=hex_id,
            lat=lat,
            lon=lon,
            network="meshtastic",
            name=node.get("longName", node.get("shortName", hex_id)),
            node_type="meshtastic_node",
            hardware=node.get("hwModel", ""),
            role=node.get("role", ""),
            battery=node.get("batteryLevel"),
            voltage=node.get("voltage"),
            channel_util=node.get("chUtil"),
            air_util_tx=node.get("airUtilTx"),
            altitude=node.get("altitude"),
            is_online=is_node_online(last_seen, "meshmap"),
            last_seen=last_seen,
            firmware=node.get("fwVersion", ""),
            region=node.get("region", ""),
            modem_preset=node.get("modemPreset", ""),
            num_online_nodes=node.get("onlineLocalNodes"),
            source="meshmap.net",
        )
