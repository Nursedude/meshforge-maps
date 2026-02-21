"""
MeshForge Maps - MQTT Live Subscriber for Meshtastic

Real-time MQTT subscription to meshtastic nodes via the public broker.
Inspired by liamcottle/meshtastic-map architecture.

Connects to mqtt.meshtastic.org and subscribes to msh/# topic tree.
Processes ServiceEnvelope protobuf packets for:
  - POSITION_APP     (portnum 3)  -> node coordinates
  - NODEINFO_APP     (portnum 4)  -> node identity
  - NEIGHBORINFO_APP (portnum 71) -> mesh topology links
  - TELEMETRY_APP    (portnum 67) -> battery, voltage, env sensors

Dependencies (optional -- graceful fallback if missing):
  - paho-mqtt: MQTT client library
  - meshtastic: protobuf definitions for ServiceEnvelope decoding

Without dependencies, falls back to reading MeshForge's MQTT cache file.

Reference: https://meshtastic.org/docs/software/integrations/mqtt/
"""

import json
import logging
import math
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# MQTT broker defaults
DEFAULT_BROKER = "mqtt.meshtastic.org"
DEFAULT_PORT = 1883
DEFAULT_TOPIC = "msh/#"
# Portnum constants (from meshtastic protobuf)
PORTNUM_POSITION = 3
PORTNUM_NODEINFO = 4
PORTNUM_TELEMETRY = 67
PORTNUM_NEIGHBORINFO = 71
PORTNUM_TRACEROUTE = 70
PORTNUM_MAP_REPORT = 73

# How long before a node is considered stale (seconds)
NODE_STALE_THRESHOLD = 3600  # 1 hour

# How long before a node is removed from the store entirely (seconds)
NODE_REMOVE_THRESHOLD = 259200  # 72 hours

# Maximum nodes to keep in memory to prevent unbounded growth
MAX_NODES = 10000

# Maximum MQTT payload size to process (bytes) -- reject oversized payloads
MAX_PAYLOAD_SIZE = 65536  # 64 KB


def _try_import_paho():
    """Try to import paho-mqtt. Returns (Client class, CallbackAPIVersion) or (None, None)."""
    try:
        import paho.mqtt.client as mqtt
        api_version = getattr(mqtt, "CallbackAPIVersion", None)
        return mqtt, api_version
    except ImportError:
        return None, None


def _try_import_meshtastic():
    """Try to import meshtastic protobuf defs. Returns module or None."""
    try:
        from meshtastic.protobuf import mqtt_pb2, mesh_pb2, portnums_pb2, telemetry_pb2
        return {
            "mqtt_pb2": mqtt_pb2,
            "mesh_pb2": mesh_pb2,
            "portnums_pb2": portnums_pb2,
            "telemetry_pb2": telemetry_pb2,
        }
    except ImportError:
        return None


def _safe_float(value: Any, low: float, high: float) -> Optional[float]:
    """Validate and clamp a numeric value to a range. Returns None if invalid."""
    if value is None:
        return None
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return None
        if not (low <= v <= high):
            return None
        return v
    except (ValueError, TypeError):
        return None


def _safe_int(value: Any, low: int, high: int) -> Optional[int]:
    """Validate and clamp an integer value to a range. Returns None if invalid."""
    if value is None:
        return None
    try:
        v = int(value)
        if not (low <= v <= high):
            return None
        return v
    except (ValueError, TypeError):
        return None


# 5-tier SNR quality classification (aligned with meshforge core topology_visualizer)
SNR_TIERS = [
    (8.0,   "excellent", "#4caf50"),   # Green
    (5.0,   "good",      "#8bc34a"),   # Light green
    (0.0,   "marginal",  "#ffeb3b"),   # Yellow
    (-10.0, "poor",      "#ff9800"),   # Orange
]
SNR_DEFAULT = ("bad", "#f44336")       # Red (SNR < -10)
SNR_UNKNOWN = ("unknown", "#9e9e9e")   # Grey (no SNR data)


def _classify_snr(snr: Optional[float]) -> tuple:
    """Classify SNR value into quality tier and color.

    Returns (quality_label, hex_color) tuple.
    """
    if snr is None:
        return SNR_UNKNOWN
    try:
        snr_val = float(snr)
    except (ValueError, TypeError):
        return SNR_UNKNOWN
    for threshold, label, color in SNR_TIERS:
        if snr_val > threshold:
            return (label, color)
    return SNR_DEFAULT


class MQTTNodeStore:
    """Thread-safe in-memory store for live MQTT node data.

    Stores nodes as dicts keyed by node ID (hex string like '!a1b2c3d4').
    Each entry contains position, identity, telemetry, and topology links.
    """

    def __init__(self, stale_seconds: int = NODE_STALE_THRESHOLD,
                 remove_seconds: int = NODE_REMOVE_THRESHOLD,
                 max_nodes: int = MAX_NODES,
                 on_node_removed: Optional[Callable[[str], None]] = None):
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._neighbors: Dict[str, List[Dict[str, Any]]] = {}  # node_id -> [{neighbor_id, snr}]
        self._lock = threading.Lock()
        self._stale_seconds = stale_seconds
        self._remove_seconds = remove_seconds
        self._max_nodes = max_nodes
        self._on_node_removed = on_node_removed

    def update_position(self, node_id: str, lat: float, lon: float,
                        altitude: Optional[int] = None, timestamp: Optional[int] = None) -> None:
        evicted_id = None
        with self._lock:
            if node_id not in self._nodes and len(self._nodes) >= self._max_nodes:
                evicted_id = self._evict_oldest_locked()
            node = self._nodes.setdefault(node_id, {"id": node_id})
            node["latitude"] = lat
            node["longitude"] = lon
            if altitude is not None:
                node["altitude"] = altitude
            node["last_seen"] = timestamp or int(time.time())
            node["is_online"] = True
        # Invoke removal callback outside lock to prevent deadlock
        cb = self._on_node_removed
        if evicted_id and cb:
            try:
                cb(evicted_id)
            except Exception as e:
                logger.debug("on_node_removed callback error: %s", e)

    def update_nodeinfo(self, node_id: str, long_name: str = "",
                        short_name: str = "", hw_model: str = "",
                        role: str = "") -> None:
        with self._lock:
            node = self._nodes.setdefault(node_id, {"id": node_id})
            if long_name:
                node["name"] = long_name
            if short_name:
                node["short_name"] = short_name
            if hw_model:
                node["hardware"] = hw_model
            if role:
                node["role"] = role
            node["last_seen"] = int(time.time())

    def update_telemetry(self, node_id: str, battery: Optional[int] = None,
                         voltage: Optional[float] = None,
                         temperature: Optional[float] = None,
                         humidity: Optional[float] = None,
                         pressure: Optional[float] = None,
                         channel_util: Optional[float] = None,
                         air_util_tx: Optional[float] = None,
                         iaq: Optional[int] = None,
                         **extra: Any) -> None:
        with self._lock:
            node = self._nodes.setdefault(node_id, {"id": node_id})
            if battery is not None:
                node["battery"] = battery
            if voltage is not None:
                node["voltage"] = voltage
            if temperature is not None:
                node["temperature"] = temperature
            if humidity is not None:
                node["humidity"] = humidity
            if pressure is not None:
                node["pressure"] = pressure
            if channel_util is not None:
                node["channel_util"] = channel_util
            if air_util_tx is not None:
                node["air_util_tx"] = air_util_tx
            if iaq is not None:
                node["iaq"] = iaq
            # Store any additional telemetry fields (air quality, health, etc.)
            for key, value in extra.items():
                if value is not None:
                    node[key] = value
            node["last_seen"] = int(time.time())

    def update_neighbors(self, node_id: str,
                         neighbors: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._neighbors[node_id] = neighbors

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Return a single node by ID, or None if not found.

        Accepts IDs with or without '!' prefix. Returns a copy.
        """
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                # Try alternate form: with/without '!' prefix
                alt = node_id.lstrip("!") if node_id.startswith("!") else f"!{node_id}"
                node = self._nodes.get(alt)
            if node is None:
                return None
            return dict(node)

    def get_all_nodes(self) -> List[Dict[str, Any]]:
        """Return all non-stale nodes with valid coordinates.

        Returns copies of node dicts; does not mutate the store.
        """
        from .base import validate_coordinates

        now = int(time.time())
        with self._lock:
            result = []
            for node in self._nodes.values():
                coords = validate_coordinates(
                    node.get("latitude"), node.get("longitude")
                )
                if coords is None:
                    continue
                copy = dict(node)
                last_seen = copy.get("last_seen", 0)
                if (now - last_seen) > self._stale_seconds:
                    copy["is_online"] = False
                result.append(copy)
            return result

    def get_topology_links(self) -> List[Dict[str, Any]]:
        """Return neighbor/link data for topology visualization."""
        from .base import validate_coordinates

        with self._lock:
            links = []
            for node_id, neighbors in self._neighbors.items():
                source = self._nodes.get(node_id, {})
                src_coords = validate_coordinates(
                    source.get("latitude"), source.get("longitude")
                )
                if src_coords is None:
                    continue
                for neighbor in neighbors:
                    nid = neighbor.get("node_id", "")
                    target = self._nodes.get(nid, {})
                    tgt_coords = validate_coordinates(
                        target.get("latitude"), target.get("longitude")
                    )
                    if tgt_coords is None:
                        continue
                    links.append({
                        "source": node_id,
                        "target": nid,
                        "source_lat": src_coords[0],
                        "source_lon": src_coords[1],
                        "target_lat": tgt_coords[0],
                        "target_lon": tgt_coords[1],
                        "snr": neighbor.get("snr"),
                    })
            return links

    def get_topology_geojson(self) -> Dict[str, Any]:
        """Return topology as a GeoJSON FeatureCollection with SNR-colored edges.

        Each link is a GeoJSON Feature with LineString geometry and properties
        including SNR value, quality tier, and color for direct rendering.

        5-tier SNR quality scale (aligned with meshforge core):
          - Excellent (SNR > 8):    #4caf50 (green)
          - Good (SNR 5-8):         #8bc34a (light green)
          - Marginal (SNR 0-5):     #ffeb3b (yellow)
          - Poor (SNR -10-0):       #ff9800 (orange)
          - Bad (SNR < -10):        #f44336 (red)
          - Unknown (no SNR):       #9e9e9e (grey)
        """
        links = self.get_topology_links()
        features = []
        for link in links:
            snr = link.get("snr")
            quality, color = _classify_snr(snr)
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
                    "source": link["source"],
                    "target": link["target"],
                    "snr": snr,
                    "quality": quality,
                    "color": color,
                },
            }
            features.append(feature)
        return {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "link_count": len(features),
            },
        }

    @property
    def node_count(self) -> int:
        with self._lock:
            return len(self._nodes)

    def cleanup_stale_nodes(self) -> int:
        """Remove nodes not seen for longer than remove_seconds.

        Returns the number of nodes removed.
        """
        now = int(time.time())
        removed_ids: List[str] = []
        with self._lock:
            stale_ids = [
                nid for nid, node in self._nodes.items()
                if (now - node.get("last_seen", 0)) > self._remove_seconds
            ]
            for nid in stale_ids:
                del self._nodes[nid]
                self._neighbors.pop(nid, None)
                removed_ids.append(nid)
        # Notify dependent modules outside the lock
        cb = self._on_node_removed
        if cb and removed_ids:
            for nid in removed_ids:
                try:
                    cb(nid)
                except Exception as e:
                    logger.debug("on_node_removed callback error: %s", e)
        if removed_ids:
            logger.debug("Cleaned up %d stale nodes from MQTT store", len(removed_ids))
        return len(removed_ids)

    def _evict_oldest_locked(self) -> Optional[str]:
        """Evict the oldest node to make room. Must be called with lock held.

        Returns the evicted node ID, or None if no eviction occurred.
        """
        if not self._nodes:
            return None
        oldest_id = min(
            self._nodes,
            key=lambda nid: self._nodes[nid].get("last_seen", 0),
        )
        del self._nodes[oldest_id]
        self._neighbors.pop(oldest_id, None)
        return oldest_id


class MQTTSubscriber:
    """Live MQTT subscriber for Meshtastic network.

    Connects to the public Meshtastic MQTT broker and processes
    ServiceEnvelope protobuf packets in real-time.

    Falls back gracefully if paho-mqtt or meshtastic packages
    are not installed.
    """

    def __init__(
        self,
        broker: str = DEFAULT_BROKER,
        port: int = DEFAULT_PORT,
        topic: str = DEFAULT_TOPIC,
        username: Optional[str] = None,
        password: Optional[str] = None,
        tls: Optional[bool] = None,
        node_store: Optional[MQTTNodeStore] = None,
        on_node_update: Optional[Callable] = None,
        event_bus: Optional[Any] = None,
    ):
        self._broker = broker
        self._port = port
        self._topic = topic
        self._username = username
        self._password = password
        # Default to TLS when credentials are provided (protect passwords)
        self._tls = tls if tls is not None else (username is not None)
        self._store = node_store or MQTTNodeStore()
        self._on_node_update = on_node_update
        self._event_bus = event_bus
        self._client = None
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._connected = threading.Event()
        self._stats_lock = threading.Lock()
        self._messages_received: int = 0
        self._parse_errors: int = 0
        self._proto = _try_import_meshtastic()

        mqtt_mod, api_version = _try_import_paho()
        self._mqtt_mod = mqtt_mod
        self._api_version = api_version

    @property
    def available(self) -> bool:
        """Whether paho-mqtt is available for live subscription."""
        return self._mqtt_mod is not None

    @property
    def store(self) -> MQTTNodeStore:
        return self._store

    def start(self) -> bool:
        """Start the MQTT subscriber in a background thread.

        Returns True if started successfully, False if dependencies missing.
        """
        if not self._mqtt_mod:
            logger.info("paho-mqtt not installed; MQTT live subscription disabled")
            return False

        if self._running.is_set():
            return True

        try:
            mqtt = self._mqtt_mod
            if self._api_version and hasattr(self._api_version, "VERSION2"):
                self._client = mqtt.Client(self._api_version.VERSION2)
            else:
                self._client = mqtt.Client()

            # Set credentials for private broker (upstream: private MQTT support)
            if self._username:
                self._client.username_pw_set(self._username, self._password or "")

            # Enable TLS for encrypted broker connections
            if self._tls:
                try:
                    import ssl
                    self._client.tls_set(cert_reqs=ssl.CERT_REQUIRED,
                                         tls_version=ssl.PROTOCOL_TLS_CLIENT)
                    logger.info("MQTT TLS enabled for %s:%d", self._broker, self._port)
                except Exception as e:
                    logger.warning("MQTT TLS setup failed: %s (continuing without TLS)", e)

            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message
            self._client.on_disconnect = self._on_disconnect

            self._running.set()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="meshforge-maps-mqtt",
                daemon=True,
            )
            self._thread.start()
            logger.info("MQTT subscriber starting: %s:%d topic=%s",
                        self._broker, self._port, self._topic)
            return True
        except Exception as e:
            logger.error("Failed to start MQTT subscriber: %s", e)
            self._running.clear()
            return False

    def stop(self) -> None:
        """Stop the MQTT subscriber gracefully."""
        self._running.clear()
        client = self._client
        if client:
            try:
                client.disconnect()
            except Exception as e:
                logger.debug("MQTT disconnect error: %s", e)
            try:
                # Use loop_stop with a timeout thread to avoid hanging
                stop_thread = threading.Thread(
                    target=client.loop_stop, daemon=True
                )
                stop_thread.start()
                stop_thread.join(timeout=5)
                if stop_thread.is_alive():
                    logger.warning("MQTT loop_stop did not complete within 5s")
            except Exception as e:
                logger.debug("MQTT loop_stop error: %s", e)
        # Wait for the main subscriber thread to exit
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("MQTT subscriber thread did not exit within 5s")
        # Safe to null these: the loop_stop daemon thread (if still alive)
        # will die with the process since it's marked daemon=True.
        self._client = None
        self._thread = None
        self._connected.clear()
        logger.info("MQTT subscriber stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Return MQTT subscriber statistics (upstream: monitoring integration)."""
        with self._stats_lock:
            messages = self._messages_received
        return {
            "broker": self._broker,
            "port": self._port,
            "topic": self._topic,
            "connected": self._connected.is_set(),
            "running": self._running.is_set(),
            "has_credentials": self._username is not None,
            "messages_received": messages,
            "parse_errors": self._parse_errors,
            "node_count": self._store.node_count,
            "protobuf_available": self._proto is not None,
        }

    def _run_loop(self) -> None:
        """Connection loop with reconnect strategy and periodic stale node cleanup."""
        from ..utils.reconnect import ReconnectStrategy

        strategy = ReconnectStrategy.for_mqtt()
        last_cleanup = time.time()
        while self._running.is_set():
            try:
                self._client.connect(self._broker, self._port, keepalive=60)
                strategy.reset()  # Reset backoff on successful connection
                self._client.loop_forever()
            except Exception as e:
                if not self._running.is_set():
                    break
                delay = strategy.next_delay()
                logger.warning(
                    "MQTT connection lost: %s, reconnecting in %.1fs (attempt %d)",
                    e,
                    delay,
                    strategy.attempt,
                )
                time.sleep(delay)

            # Periodic stale node cleanup (every 30 minutes)
            now = time.time()
            if (now - last_cleanup) > 1800:
                self._store.cleanup_stale_nodes()
                last_cleanup = now

    def _on_connect(self, client: Any, userdata: Any, flags: Any,
                    rc: Any, *args: Any) -> None:
        self._connected.set()
        logger.info("MQTT connected to %s (rc=%s), store has %d nodes",
                    self._broker, rc, self._store.node_count)
        client.subscribe(self._topic)

    def _on_disconnect(self, client: Any, userdata: Any, rc: Any,
                       *args: Any) -> None:
        self._connected.clear()
        if self._running.is_set():
            logger.warning("MQTT disconnected (rc=%s), will reconnect", rc)

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        """Process incoming MQTT message."""
        # Reject oversized payloads with warning (upstream improvement)
        if len(msg.payload) > MAX_PAYLOAD_SIZE:
            # Sanitize topic for logging: strip node-specific segments for privacy
            topic_parts = msg.topic.split("/")
            safe_topic = "/".join(topic_parts[:5]) + "/..." if len(topic_parts) > 5 else msg.topic
            logger.warning(
                "MQTT: rejected oversized payload (%d bytes) on %s",
                len(msg.payload), safe_topic,
            )
            return

        with self._stats_lock:
            self._messages_received += 1

        try:
            # Try protobuf decoding first
            if self._proto:
                self._decode_protobuf(msg.payload, msg.topic)
            else:
                # Fallback: try JSON (if device has JSON mode enabled)
                self._decode_json(msg.payload, msg.topic)
        except (ValueError, TypeError, KeyError, AttributeError):
            # Unparseable messages are common on the public broker
            self._parse_errors += 1
            if self._parse_errors % 1000 == 0:
                logger.warning(
                    "MQTT: %d total unparseable messages dropped",
                    self._parse_errors,
                )
        except Exception as e:
            self._parse_errors += 1
            logger.debug("MQTT message processing error on %s: %s", msg.topic, e)

    def _notify_update(self, node_id: str, update_type: str, **kwargs) -> None:
        """Safely invoke the on_node_update callback and publish to event bus."""
        cb = self._on_node_update
        if cb:
            try:
                cb(node_id, update_type)
            except Exception as e:
                logger.debug("on_node_update callback error: %s", e)

        bus = self._event_bus
        if bus:
            self._emit_event(bus, node_id, update_type, **kwargs)

    def _emit_event(self, bus, node_id: str, update_type: str, **kwargs) -> None:
        """Publish a typed event to the event bus."""
        try:
            from ..utils.event_bus import NodeEvent
            factories = {
                "position": NodeEvent.position,
                "nodeinfo": NodeEvent.info,
                "telemetry": NodeEvent.telemetry,
                "topology": NodeEvent.topology,
            }
            factory = factories.get(update_type)
            if factory:
                bus.publish(factory(node_id, source="mqtt", **kwargs))
        except Exception as e:
            logger.debug("Event bus publish error: %s", e)

    def _decode_protobuf(self, payload: bytes, topic: str) -> None:
        """Decode ServiceEnvelope protobuf message."""
        mqtt_pb2 = self._proto["mqtt_pb2"]
        mesh_pb2 = self._proto["mesh_pb2"]

        env = mqtt_pb2.ServiceEnvelope()
        env.ParseFromString(payload)

        if not env.packet:
            return

        packet = env.packet
        from_node = f"!{packet.sender:08x}" if hasattr(packet, "sender") else f"!{getattr(packet, 'from', 0):08x}"

        if not hasattr(packet, "decoded") or not packet.decoded:
            return  # Encrypted packet we can't decode

        decoded = packet.decoded
        portnum = decoded.portnum

        if portnum == PORTNUM_POSITION:
            self._handle_position(from_node, decoded.payload)
        elif portnum == PORTNUM_NODEINFO:
            self._handle_nodeinfo(from_node, decoded.payload)
        elif portnum == PORTNUM_TELEMETRY:
            self._handle_telemetry(from_node, decoded.payload)
        elif portnum == PORTNUM_NEIGHBORINFO:
            self._handle_neighborinfo(from_node, decoded.payload)

    def _handle_position(self, node_id: str, payload: bytes) -> None:
        mesh_pb2 = self._proto["mesh_pb2"]
        pos = mesh_pb2.Position()
        pos.ParseFromString(payload)

        lat = pos.latitude_i / 1e7 if pos.latitude_i != 0 else 0.0
        lon = pos.longitude_i / 1e7 if pos.longitude_i != 0 else 0.0

        if (lat is not None and lon is not None
                and (-90 <= lat <= 90) and (-180 <= lon <= 180)
                and not (abs(lat) < 0.01 and abs(lon) < 0.01)):
            alt = _safe_int(pos.altitude, -500, 100000) if pos.altitude != 0 else 0
            self._store.update_position(node_id, lat, lon, altitude=alt)
            self._notify_update(node_id, "position", lat=lat, lon=lon)

    def _handle_nodeinfo(self, node_id: str, payload: bytes) -> None:
        mesh_pb2 = self._proto["mesh_pb2"]
        info = mesh_pb2.User()
        info.ParseFromString(payload)

        self._store.update_nodeinfo(
            node_id,
            long_name=info.long_name,
            short_name=info.short_name,
            hw_model=str(info.hw_model) if info.hw_model else "",
            role=str(info.role) if info.role else "",
        )
        self._notify_update(
            node_id, "nodeinfo",
            long_name=info.long_name,
            short_name=info.short_name,
        )

    def _handle_telemetry(self, node_id: str, payload: bytes) -> None:
        telemetry_pb2 = self._proto["telemetry_pb2"]
        telem = telemetry_pb2.Telemetry()
        telem.ParseFromString(payload)

        if telem.HasField("device_metrics"):
            dm = telem.device_metrics
            battery = _safe_int(dm.battery_level, 0, 100)
            voltage = _safe_float(dm.voltage, 0.0, 100.0)
            channel_util = _safe_float(
                getattr(dm, "channel_utilization", None), 0.0, 100.0
            )
            air_util_tx = _safe_float(
                getattr(dm, "air_util_tx", None), 0.0, 100.0
            )
            self._store.update_telemetry(
                node_id,
                battery=battery,
                voltage=voltage,
                channel_util=channel_util,
                air_util_tx=air_util_tx,
            )

        # Environmental sensors (temperature, humidity, pressure, IAQ)
        if telem.HasField("environment_metrics"):
            em = telem.environment_metrics
            temperature = _safe_float(
                getattr(em, "temperature", None), -100.0, 200.0
            )
            humidity = _safe_float(
                getattr(em, "relative_humidity", None), 0.0, 100.0
            )
            pressure = _safe_float(
                getattr(em, "barometric_pressure", None), 0.0, 2000.0
            )
            iaq = _safe_int(getattr(em, "iaq", None), 0, 500)
            self._store.update_telemetry(
                node_id,
                temperature=temperature,
                humidity=humidity,
                pressure=pressure,
                iaq=iaq,
            )

        # Air quality sensors (PM2.5, PM10, CO2, VOC, NOx)
        if telem.HasField("air_quality_metrics"):
            aq = telem.air_quality_metrics
            self._store.update_telemetry(
                node_id,
                pm10_standard=_safe_int(getattr(aq, "pm10_standard", None), 0, 10000),
                pm25_standard=_safe_int(getattr(aq, "pm25_standard", None), 0, 10000),
                pm100_standard=_safe_int(getattr(aq, "pm100_standard", None), 0, 10000),
                pm10_environmental=_safe_int(getattr(aq, "pm10_environmental", None), 0, 10000),
                pm25_environmental=_safe_int(getattr(aq, "pm25_environmental", None), 0, 10000),
                pm100_environmental=_safe_int(getattr(aq, "pm100_environmental", None), 0, 10000),
                co2=_safe_int(getattr(aq, "co2", None), 0, 40000),
                pm_voc_idx=_safe_float(getattr(aq, "pm_voc_idx", None), 0.0, 500.0),
                pm_nox_idx=_safe_float(getattr(aq, "pm_nox_idx", None), 0.0, 500.0),
            )

        # Health sensors (heart rate, SpO2, body temperature)
        if telem.HasField("health_metrics"):
            hm = telem.health_metrics
            self._store.update_telemetry(
                node_id,
                heart_bpm=_safe_int(getattr(hm, "heart_bpm", None), 0, 300),
                spo2=_safe_int(getattr(hm, "spO2", None), 0, 100),
                body_temperature=_safe_float(
                    getattr(hm, "temperature", None), 20.0, 50.0
                ),
            )

        self._notify_update(node_id, "telemetry")

    def _handle_neighborinfo(self, node_id: str, payload: bytes) -> None:
        mesh_pb2 = self._proto["mesh_pb2"]
        ni = mesh_pb2.NeighborInfo()
        ni.ParseFromString(payload)

        neighbors = []
        for n in ni.neighbors:
            neighbors.append({
                "node_id": f"!{n.node_id:08x}",
                "snr": float(n.snr),
            })
        self._store.update_neighbors(node_id, neighbors)
        self._notify_update(node_id, "topology", neighbor_count=len(neighbors))

    def _decode_json(self, payload: bytes, topic: str) -> None:
        """Fallback: try to decode as JSON (when device has JSON MQTT enabled)."""
        try:
            data = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        # JSON format from Meshtastic firmware
        sender = data.get("sender", data.get("from", ""))
        if isinstance(sender, int):
            sender = f"!{sender:08x}"

        payload_data = data.get("payload", {})
        msg_type = data.get("type", "")

        if msg_type == "position" or "latitude_i" in payload_data:
            lat_i = payload_data.get("latitude_i", 0)
            lon_i = payload_data.get("longitude_i", 0)
            if lat_i and lon_i:
                lat = lat_i / 1e7
                lon = lon_i / 1e7
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    self._store.update_position(
                        sender, lat, lon,
                        altitude=payload_data.get("altitude"),
                    )

        if msg_type == "nodeinfo":
            self._store.update_nodeinfo(
                sender,
                long_name=payload_data.get("long_name", ""),
                short_name=payload_data.get("short_name", ""),
                hw_model=payload_data.get("hw_model", ""),
            )
