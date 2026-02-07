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
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# MQTT broker defaults
DEFAULT_BROKER = "mqtt.meshtastic.org"
DEFAULT_PORT = 1883
DEFAULT_TOPIC = "msh/#"
DEFAULT_KEY = "AQ=="  # Default Meshtastic encryption key (base64)

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


class MQTTNodeStore:
    """Thread-safe in-memory store for live MQTT node data.

    Stores nodes as dicts keyed by node ID (hex string like '!a1b2c3d4').
    Each entry contains position, identity, telemetry, and topology links.
    """

    def __init__(self, stale_seconds: int = NODE_STALE_THRESHOLD,
                 remove_seconds: int = NODE_REMOVE_THRESHOLD,
                 max_nodes: int = MAX_NODES):
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._neighbors: Dict[str, List[Dict[str, Any]]] = {}  # node_id -> [{neighbor_id, snr}]
        self._lock = threading.Lock()
        self._stale_seconds = stale_seconds
        self._remove_seconds = remove_seconds
        self._max_nodes = max_nodes

    def update_position(self, node_id: str, lat: float, lon: float,
                        altitude: Optional[int] = None, timestamp: Optional[int] = None) -> None:
        with self._lock:
            if node_id not in self._nodes and len(self._nodes) >= self._max_nodes:
                self._evict_oldest_locked()
            node = self._nodes.setdefault(node_id, {"id": node_id})
            node["latitude"] = lat
            node["longitude"] = lon
            if altitude is not None:
                node["altitude"] = altitude
            node["last_seen"] = timestamp or int(time.time())
            node["is_online"] = True

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
                         temperature: Optional[float] = None) -> None:
        with self._lock:
            node = self._nodes.setdefault(node_id, {"id": node_id})
            if battery is not None:
                node["battery"] = battery
            if voltage is not None:
                node["voltage"] = voltage
            if temperature is not None:
                node["temperature"] = temperature
            node["last_seen"] = int(time.time())

    def update_neighbors(self, node_id: str,
                         neighbors: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._neighbors[node_id] = neighbors

    def get_all_nodes(self) -> List[Dict[str, Any]]:
        """Return all non-stale nodes with valid coordinates."""
        now = int(time.time())
        with self._lock:
            result = []
            for node in self._nodes.values():
                lat = node.get("latitude")
                lon = node.get("longitude")
                if lat is None or lon is None:
                    continue
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
                last_seen = node.get("last_seen", 0)
                if (now - last_seen) > self._stale_seconds:
                    node["is_online"] = False
                result.append(dict(node))
            return result

    def get_topology_links(self) -> List[Dict[str, Any]]:
        """Return neighbor/link data for topology visualization."""
        with self._lock:
            links = []
            for node_id, neighbors in self._neighbors.items():
                source = self._nodes.get(node_id, {})
                if not (source.get("latitude") and source.get("longitude")):
                    continue
                for neighbor in neighbors:
                    nid = neighbor.get("node_id", "")
                    target = self._nodes.get(nid, {})
                    if target.get("latitude") and target.get("longitude"):
                        links.append({
                            "source": node_id,
                            "target": nid,
                            "source_lat": source["latitude"],
                            "source_lon": source["longitude"],
                            "target_lat": target["latitude"],
                            "target_lon": target["longitude"],
                            "snr": neighbor.get("snr"),
                        })
            return links

    @property
    def node_count(self) -> int:
        with self._lock:
            return len(self._nodes)

    def cleanup_stale_nodes(self) -> int:
        """Remove nodes not seen for longer than remove_seconds.

        Returns the number of nodes removed.
        """
        now = int(time.time())
        removed = 0
        with self._lock:
            stale_ids = [
                nid for nid, node in self._nodes.items()
                if (now - node.get("last_seen", 0)) > self._remove_seconds
            ]
            for nid in stale_ids:
                del self._nodes[nid]
                self._neighbors.pop(nid, None)
                removed += 1
        if removed:
            logger.debug("Cleaned up %d stale nodes from MQTT store", removed)
        return removed

    def _evict_oldest_locked(self) -> None:
        """Evict the oldest node to make room. Must be called with lock held."""
        if not self._nodes:
            return
        oldest_id = min(
            self._nodes,
            key=lambda nid: self._nodes[nid].get("last_seen", 0),
        )
        del self._nodes[oldest_id]
        self._neighbors.pop(oldest_id, None)


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
        node_store: Optional[MQTTNodeStore] = None,
        on_node_update: Optional[Callable] = None,
    ):
        self._broker = broker
        self._port = port
        self._topic = topic
        self._store = node_store or MQTTNodeStore()
        self._on_node_update = on_node_update
        self._client = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
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

        if self._running:
            return True

        try:
            mqtt = self._mqtt_mod
            if self._api_version and hasattr(self._api_version, "VERSION2"):
                self._client = mqtt.Client(self._api_version.VERSION2)
            else:
                self._client = mqtt.Client()

            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message
            self._client.on_disconnect = self._on_disconnect

            self._running = True
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
            self._running = False
            return False

    def stop(self) -> None:
        """Stop the MQTT subscriber gracefully."""
        self._running = False
        client = self._client
        if client:
            try:
                client.disconnect()
                # Use loop_stop with a timeout thread to avoid hanging
                stop_thread = threading.Thread(
                    target=client.loop_stop, daemon=True
                )
                stop_thread.start()
                stop_thread.join(timeout=5)
            except Exception:
                pass
        self._client = None
        logger.info("MQTT subscriber stopped")

    def _run_loop(self) -> None:
        """Connection loop with reconnect logic and periodic stale node cleanup."""
        backoff = 2
        last_cleanup = time.time()
        while self._running:
            try:
                self._client.connect(self._broker, self._port, keepalive=60)
                backoff = 2  # Reset backoff on successful connection
                self._client.loop_forever()
            except Exception as e:
                if not self._running:
                    break
                # Add jitter to prevent thundering herd on reconnect
                jitter = random.uniform(0, backoff * 0.25)
                wait = backoff + jitter
                logger.warning("MQTT connection lost: %s, reconnecting in %.1fs", e, wait)
                time.sleep(wait)
                backoff = min(backoff * 2, 60)

            # Periodic stale node cleanup (every 30 minutes)
            now = time.time()
            if (now - last_cleanup) > 1800:
                self._store.cleanup_stale_nodes()
                last_cleanup = now

    def _on_connect(self, client: Any, userdata: Any, flags: Any,
                    rc: Any, *args: Any) -> None:
        logger.info("MQTT connected to %s (rc=%s), store has %d nodes",
                    self._broker, rc, self._store.node_count)
        client.subscribe(self._topic)

    def _on_disconnect(self, client: Any, userdata: Any, rc: Any,
                       *args: Any) -> None:
        if self._running:
            logger.warning("MQTT disconnected (rc=%s), will reconnect", rc)

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        """Process incoming MQTT message."""
        try:
            # Try protobuf decoding first
            if self._proto:
                self._decode_protobuf(msg.payload, msg.topic)
            else:
                # Fallback: try JSON (if device has JSON mode enabled)
                self._decode_json(msg.payload, msg.topic)
        except (ValueError, TypeError, KeyError, AttributeError):
            # Unparseable messages are common on the public broker -- expected
            pass
        except Exception as e:
            # Log unexpected errors at debug level to aid diagnosis
            # without flooding logs (the broker sends many messages)
            logger.debug("MQTT message processing error on %s: %s", msg.topic, e)

    def _notify_update(self, node_id: str, update_type: str) -> None:
        """Safely invoke the on_node_update callback."""
        cb = self._on_node_update
        if cb:
            try:
                cb(node_id, update_type)
            except Exception as e:
                logger.debug("on_node_update callback error: %s", e)

    def _decode_protobuf(self, payload: bytes, topic: str) -> None:
        """Decode ServiceEnvelope protobuf message."""
        mqtt_pb2 = self._proto["mqtt_pb2"]
        mesh_pb2 = self._proto["mesh_pb2"]

        env = mqtt_pb2.ServiceEnvelope()
        env.ParseFromString(payload)

        if not env.packet:
            return

        packet = env.packet
        from_id = f"!{packet.id:08x}" if packet.id else None
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

        lat = pos.latitude_i / 1e7 if pos.latitude_i else None
        lon = pos.longitude_i / 1e7 if pos.longitude_i else None

        if lat is not None and lon is not None and (-90 <= lat <= 90) and (-180 <= lon <= 180):
            alt = _safe_int(pos.altitude, -500, 100000) if pos.altitude else None
            self._store.update_position(node_id, lat, lon, altitude=alt)
            self._notify_update(node_id, "position")

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

    def _handle_telemetry(self, node_id: str, payload: bytes) -> None:
        telemetry_pb2 = self._proto["telemetry_pb2"]
        telem = telemetry_pb2.Telemetry()
        telem.ParseFromString(payload)

        if telem.HasField("device_metrics"):
            dm = telem.device_metrics
            battery = _safe_int(dm.battery_level, 0, 100)
            voltage = _safe_float(dm.voltage, 0.0, 100.0)
            self._store.update_telemetry(
                node_id,
                battery=battery,
                voltage=voltage,
            )

        # Environmental sensors (temperature, humidity, pressure)
        if telem.HasField("environment_metrics"):
            em = telem.environment_metrics
            temperature = _safe_float(
                getattr(em, "temperature", None), -100.0, 200.0
            )
            humidity = _safe_float(
                getattr(em, "relative_humidity", None), 0.0, 100.0
            )
            if temperature is not None or humidity is not None:
                self._store.update_telemetry(
                    node_id,
                    temperature=temperature,
                )

    def _handle_neighborinfo(self, node_id: str, payload: bytes) -> None:
        mesh_pb2 = self._proto["mesh_pb2"]
        ni = mesh_pb2.NeighborInfo()
        ni.ParseFromString(payload)

        neighbors = []
        for n in ni.neighbors:
            neighbors.append({
                "node_id": f"!{n.node_id:08x}",
                "snr": n.snr if n.snr else None,
            })
        self._store.update_neighbors(node_id, neighbors)

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
