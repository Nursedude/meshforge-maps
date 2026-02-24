"""
MeshForge Maps - Meshtastic API Proxy

HTTP server that proxies meshtasticd-compatible API endpoints backed by
the MQTT subscriber's in-memory MQTTNodeStore. Enables tools that expect
a local meshtasticd instance to work with MQTT-sourced data.

Standard meshtasticd only exposes binary protobuf endpoints
(GET /api/v1/fromradio, PUT /api/v1/toradio). This proxy provides a
JSON REST API at /api/v1/nodes that is easier for dashboards, scripts,
and web tools to consume.

The proxy is read-only: it serves node data from the live MQTT store but
does not forward commands to the mesh. Write support (toradio) could be
added later if a local meshtasticd is available for command relay.

Thread-safe: runs in a background thread with its own HTTP server.
Graceful degradation: returns empty responses when MQTT store is unavailable.

Endpoints:
  GET /api/v1/nodes          - All nodes as JSON array
  GET /api/v1/nodes/<node_id> - Single node by ID
  GET /api/v1/topology       - Mesh topology links
  GET /api/v1/stats          - Proxy statistics
"""

import json
import logging
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..collectors.base import NODE_ID_RE

logger = logging.getLogger(__name__)

# Default port matches meshtasticd convention
DEFAULT_PROXY_PORT = 4404  # Adjacent to meshtasticd's 4403


class ProxyHTTPServer(HTTPServer):
    """HTTPServer subclass with typed attributes for the Meshtastic API proxy.

    Replaces the previous pattern of monkey-patching _mf_mqtt_store and
    _mf_proxy onto a bare HTTPServer instance.
    """

    def __init__(self, server_address: tuple, handler_class: type,
                 mqtt_store: Optional[Any] = None,
                 proxy: Optional["MeshtasticApiProxy"] = None) -> None:
        super().__init__(server_address, handler_class)
        self.mqtt_store = mqtt_store
        self.proxy = proxy


class MeshtasticApiProxyHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Meshtastic API proxy."""

    server: ProxyHTTPServer  # type annotation for IDE support

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        try:
            if path == "/api/v1/nodes":
                self._serve_nodes()
            elif path.startswith("/api/v1/nodes/"):
                node_id = path.split("/")[-1]
                if not NODE_ID_RE.match(node_id):
                    self._send_json({"error": "Invalid node ID format"}, 400)
                else:
                    self._serve_node(node_id)
            elif path == "/api/v1/topology":
                self._serve_topology()
            elif path == "/api/v1/stats":
                self._serve_stats()
            else:
                self._send_json({"error": "Not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            logger.error("Proxy handler error for %s: %s", self.path, e)
            try:
                self._send_json({"error": "Internal server error"}, 500)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _get_cors_origin(self):
        """Get configured CORS origin, or None for same-origin."""
        proxy = self.server.proxy
        return proxy._cors_origin if proxy else None

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        cors_origin = self._get_cors_origin()
        self.send_response(204)
        if cors_origin:
            self.send_header("Access-Control-Allow-Origin", cors_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
            self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def _get_store(self):
        """Get the MQTTNodeStore from the server instance."""
        return self.server.mqtt_store

    def _get_proxy(self):
        """Get the MeshtasticApiProxy from the server instance."""
        return self.server.proxy

    def _serve_nodes(self) -> None:
        """Serve all nodes as a JSON array in meshtasticd-compatible format."""
        store = self._get_store()
        proxy = self._get_proxy()
        if proxy:
            proxy._inc_request_count()

        if not store:
            self._send_json({"nodes": [], "node_count": 0})
            return

        nodes = store.get_all_nodes()
        formatted = []
        for node in nodes:
            formatted.append(_format_node_meshtastic(node))

        self._send_json({
            "nodes": formatted,
            "node_count": len(formatted),
            "source": "mqtt_proxy",
        })

    def _serve_node(self, node_id: str) -> None:
        """Serve a single node by ID.

        Uses direct O(1) lookup via store.get_node() when available,
        falling back to linear scan for older store implementations.
        """
        store = self._get_store()
        proxy = self._get_proxy()
        if proxy:
            proxy._inc_request_count()

        if not store:
            self._send_json({"error": "Store not available"}, 503)
            return

        # Prefer O(1) lookup if the store supports it
        if hasattr(store, "get_node"):
            node = store.get_node(node_id)
            if node is not None:
                self._send_json(_format_node_meshtastic(node))
                return
        else:
            # Fallback: linear scan for stores without get_node()
            nodes = store.get_all_nodes()
            for node in nodes:
                nid = node.get("id", "")
                if nid == node_id or nid.lstrip("!") == node_id.lstrip("!"):
                    self._send_json(_format_node_meshtastic(node))
                    return

        self._send_json({"error": "Node not found"}, 404)

    def _serve_topology(self) -> None:
        """Serve mesh topology links."""
        store = self._get_store()
        proxy = self._get_proxy()
        if proxy:
            proxy._inc_request_count()

        if not store:
            self._send_json({"links": [], "link_count": 0})
            return

        links = store.get_topology_links()
        self._send_json({
            "links": links,
            "link_count": len(links),
        })

    def _serve_stats(self) -> None:
        """Serve proxy statistics."""
        store = self._get_store()
        proxy = self._get_proxy()

        stats = {
            "proxy_running": True,
            "store_available": store is not None,
            "node_count": store.node_count if store else 0,
            "request_count": proxy.request_count if proxy else 0,
            "uptime_seconds": int(time.time() - proxy._start_time) if proxy else 0,
        }
        self._send_json(stats)

    def _send_json(self, data: Any, status: int = 200) -> None:
        try:
            body = json.dumps(data, default=str).encode("utf-8")
        except (TypeError, ValueError) as e:
            logger.error("JSON serialization error: %s", e)
            body = b'{"error": "serialization error"}'
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        cors_origin = self._get_cors_origin()
        if cors_origin:
            self.send_header("Access-Control-Allow-Origin", cors_origin)
        self.end_headers()
        self.wfile.write(body)

    server_version = "MeshForge-Proxy/1.0"

    def version_string(self) -> str:
        return self.server_version

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("Proxy HTTP %s", format % args)


def _format_node_meshtastic(node: Dict[str, Any]) -> Dict[str, Any]:
    """Format an MQTTNodeStore node dict into meshtasticd-compatible JSON.

    Output mimics the meshtastic Python library's node dict structure:
    {
        "num": <int>,
        "user": {"id": ..., "longName": ..., "shortName": ..., "hwModel": ..., "role": ...},
        "position": {"latitude": ..., "longitude": ..., "altitude": ...},
        "deviceMetrics": {"batteryLevel": ..., "voltage": ..., "channelUtilization": ...},
        "snr": ...,
        "lastHeard": ...,
        "hopsAway": ...,
        "viaMqtt": ...,
        "environmentMetrics": {...},
        "airQualityMetrics": {...},
        "healthMetrics": {...},
    }
    """
    node_id = node.get("id", "")
    # Convert hex node ID to integer (e.g., "!a1b2c3d4" -> 2712862676)
    num = 0
    if node_id.startswith("!"):
        try:
            num = int(node_id[1:], 16)
        except ValueError:
            pass

    result: Dict[str, Any] = {
        "num": num,
        "user": {
            "id": node_id,
            "longName": node.get("name", node_id),
            "shortName": node.get("short_name", ""),
            "hwModel": node.get("hardware", ""),
            "role": node.get("role", ""),
        },
        "lastHeard": node.get("last_seen"),
        "snr": node.get("snr"),
    }

    # Position
    lat = node.get("latitude")
    lon = node.get("longitude")
    if lat is not None and lon is not None:
        pos: Dict[str, Any] = {"latitude": lat, "longitude": lon}
        alt = node.get("altitude")
        if alt is not None:
            pos["altitude"] = alt
        result["position"] = pos

    # Device metrics
    dm: Dict[str, Any] = {}
    if node.get("battery") is not None:
        dm["batteryLevel"] = node["battery"]
    if node.get("voltage") is not None:
        dm["voltage"] = node["voltage"]
    if node.get("channel_util") is not None:
        dm["channelUtilization"] = node["channel_util"]
    if node.get("air_util_tx") is not None:
        dm["airUtilTx"] = node["air_util_tx"]
    if dm:
        result["deviceMetrics"] = dm

    # Environment metrics
    em: Dict[str, Any] = {}
    if node.get("temperature") is not None:
        em["temperature"] = node["temperature"]
    if node.get("humidity") is not None:
        em["relativeHumidity"] = node["humidity"]
    if node.get("pressure") is not None:
        em["barometricPressure"] = node["pressure"]
    if node.get("iaq") is not None:
        em["iaq"] = node["iaq"]
    if em:
        result["environmentMetrics"] = em

    # Air quality metrics
    aq: Dict[str, Any] = {}
    for key in ("pm25_standard", "pm100_standard", "pm10_standard",
                "co2", "pm_voc_idx", "pm_nox_idx",
                "pm25_environmental", "pm100_environmental",
                "pm10_environmental"):
        if node.get(key) is not None:
            aq[key] = node[key]
    if aq:
        result["airQualityMetrics"] = aq

    # Health metrics
    hm: Dict[str, Any] = {}
    if node.get("heart_bpm") is not None:
        hm["heartBpm"] = node["heart_bpm"]
    if node.get("spo2") is not None:
        hm["spO2"] = node["spo2"]
    if node.get("body_temperature") is not None:
        hm["temperature"] = node["body_temperature"]
    if hm:
        result["healthMetrics"] = hm

    # Optional fields
    if node.get("hops_away") is not None:
        result["hopsAway"] = node["hops_away"]
    if node.get("via_mqtt") is not None:
        result["viaMqtt"] = node["via_mqtt"]

    return result


class MeshtasticApiProxy:
    """HTTP proxy server serving meshtasticd-compatible JSON API.

    Reads from an MQTTNodeStore and serves JSON endpoints that tools
    expecting a local meshtasticd can consume.

    Usage:
        proxy = MeshtasticApiProxy(mqtt_store=subscriber.store, port=4404)
        proxy.start()
        ...
        proxy.stop()
    """

    def __init__(
        self,
        mqtt_store: Optional[Any] = None,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PROXY_PORT,
        cors_origin: Optional[str] = None,
    ):
        self._mqtt_store = mqtt_store
        self._host = host
        self._port = port
        self._cors_origin = cors_origin
        self._server: Optional[ProxyHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._request_count = 0
        self._request_count_lock = threading.Lock()
        self._start_time = 0.0

    def _inc_request_count(self) -> None:
        """Thread-safe request counter increment."""
        with self._request_count_lock:
            self._request_count += 1

    @property
    def request_count(self) -> int:
        with self._request_count_lock:
            return self._request_count

    @property
    def port(self) -> int:
        """The actual port the proxy bound to (0 if not started)."""
        return self._port if self._running else 0

    @property
    def running(self) -> bool:
        return self._running

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "host": self._host,
            "port": self._port if self._running else 0,
            "request_count": self.request_count,
            "uptime_seconds": int(time.time() - self._start_time) if self._running else 0,
            "store_available": self._mqtt_store is not None,
            "node_count": self._mqtt_store.node_count if self._mqtt_store else 0,
        }

    def set_store(self, store: Any) -> None:
        """Update the MQTT node store reference (for late binding).

        Both the proxy and the server attributes are updated atomically
        so that in-flight request handlers see a consistent store.
        """
        self._mqtt_store = store
        server = self._server
        if server is not None:
            server.mqtt_store = store

    def start(self) -> bool:
        """Start the proxy server in a background thread.

        Tries the configured port, then falls back to 4 adjacent ports.
        Returns True if started, False otherwise.
        """
        if self._running:
            return True

        for offset in range(5):
            port = self._port + offset
            try:
                self._server = ProxyHTTPServer(
                    (self._host, port), MeshtasticApiProxyHandler,
                    mqtt_store=self._mqtt_store,
                    proxy=self,
                )

                self._thread = threading.Thread(
                    target=self._serve_forever_safe,
                    name="meshforge-maps-meshtastic-proxy",
                    daemon=True,
                )
                self._thread.start()
                self._port = port
                self._running = True
                self._start_time = time.time()

                if offset > 0:
                    logger.warning(
                        "Proxy port %d in use, started on http://%s:%d",
                        self._port - offset, self._host, port,
                    )
                else:
                    logger.info(
                        "Meshtastic API proxy started on http://%s:%d",
                        self._host, port,
                    )
                return True
            except OSError as e:
                logger.debug("Proxy port %d unavailable: %s", port, e)
                continue

        logger.error("Failed to start Meshtastic API proxy on ports %d-%d",
                      self._port, self._port + 4)
        return False

    def _serve_forever_safe(self) -> None:
        """Wrapper around serve_forever that sets _running=False on failure."""
        try:
            if self._server:
                self._server.serve_forever()
        except Exception as e:
            logger.error("Meshtastic API proxy serve_forever failed: %s", e)
        finally:
            self._running = False

    def stop(self) -> None:
        """Stop the proxy server."""
        self._running = False
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("Proxy server thread did not exit within 5s")
        self._thread = None
        logger.info("Meshtastic API proxy stopped")
