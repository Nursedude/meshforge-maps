"""
MeshForge Maps - HTTP Map Server

Lightweight HTTP server that serves:
  - The Leaflet.js web map frontend
  - GeoJSON API endpoints for node data
  - Configuration and overlay data endpoints

Follows meshforge patterns: SimpleHTTPRequestHandler, no-cache headers,
CORS support for local development.
"""

import json
import logging
import os
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .collectors.aggregator import DataAggregator
from .utils.config import NETWORK_COLORS, TILE_PROVIDERS, MapsConfig

logger = logging.getLogger(__name__)


class MapRequestHandler(SimpleHTTPRequestHandler):
    """HTTP request handler for MeshForge Maps.

    Instance-level state is injected via the server reference rather than
    class-level attributes, preventing multiple MapServer instances from
    clobbering each other's state.
    """

    def _get_aggregator(self) -> Optional[DataAggregator]:
        return getattr(self.server, "_mf_aggregator", None)

    def _get_config(self) -> Optional[MapsConfig]:
        return getattr(self.server, "_mf_config", None)

    def _get_web_dir(self) -> Optional[str]:
        return getattr(self.server, "_mf_web_dir", None)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        routes = {
            "": self._serve_map,
            "/index.html": self._serve_map,
            "/api/nodes/geojson": self._serve_geojson,
            "/api/nodes/all": self._serve_geojson,
            "/api/config": self._serve_config,
            "/api/tile-providers": self._serve_tile_providers,
            "/api/sources": self._serve_sources,
            "/api/overlay": self._serve_overlay,
            "/api/topology": self._serve_topology,
            "/api/status": self._serve_status,
        }

        handler = routes.get(path)
        try:
            if handler:
                handler()
            elif path.startswith("/api/nodes/"):
                # /api/nodes/<source_name>
                source = path.split("/")[-1]
                self._serve_source_geojson(source)
            else:
                # Serve static files from web directory
                web_dir = self._get_web_dir()
                if web_dir:
                    self.directory = web_dir
                super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected before response completed -- expected
            pass
        except Exception as e:
            logger.error("Request handler error for %s: %s", self.path, e)
            try:
                self._send_json({"error": "Internal server error"}, 500)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def _serve_map(self) -> None:
        """Serve the main map HTML page."""
        map_path = self._find_map_file()
        if map_path and map_path.exists():
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            with open(map_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404, "Map file not found")

    def _serve_geojson(self) -> None:
        """Serve aggregated GeoJSON from all enabled sources."""
        aggregator = self._get_aggregator()
        if not aggregator:
            self._send_json({"error": "Aggregator not initialized"}, 503)
            return
        data = aggregator.collect_all()
        self._send_json(data)

    def _serve_source_geojson(self, source: str) -> None:
        """Serve GeoJSON from a single source."""
        aggregator = self._get_aggregator()
        if not aggregator:
            self._send_json({"error": "Aggregator not initialized"}, 503)
            return
        data = aggregator.collect_source(source)
        self._send_json(data)

    def _serve_config(self) -> None:
        """Serve current configuration (non-sensitive)."""
        config = self._get_config()
        if not config:
            self._send_json({})
            return
        cfg = config.to_dict()
        cfg["network_colors"] = NETWORK_COLORS
        self._send_json(cfg)

    def _serve_tile_providers(self) -> None:
        """Serve available tile provider definitions."""
        self._send_json(TILE_PROVIDERS)

    def _serve_sources(self) -> None:
        """Serve list of enabled data sources."""
        config = self._get_config()
        if not config:
            self._send_json({"sources": []})
            return
        self._send_json({
            "sources": config.get_enabled_sources(),
            "network_colors": NETWORK_COLORS,
        })

    def _serve_overlay(self) -> None:
        """Serve overlay data (space weather, terminator).

        Uses cached overlay from the last collect_all() to avoid a
        redundant heavy aggregation call on every overlay request.
        """
        aggregator = self._get_aggregator()
        if not aggregator:
            self._send_json({})
            return
        overlay = aggregator.get_cached_overlay()
        self._send_json(overlay)

    def _serve_topology(self) -> None:
        """Serve topology link data for D3.js force graph."""
        aggregator = self._get_aggregator()
        if not aggregator:
            self._send_json({"links": []})
            return
        links = aggregator.get_topology_links()
        self._send_json({"links": links, "link_count": len(links)})

    def _serve_status(self) -> None:
        """Serve server health status with uptime, data age, and node store stats."""
        aggregator = self._get_aggregator()
        config = self._get_config()
        mqtt_status = "unavailable"
        mqtt_nodes = 0
        if aggregator and aggregator._mqtt_subscriber:
            mqtt_status = "connected" if aggregator._mqtt_subscriber._running else "stopped"
            mqtt_nodes = aggregator._mqtt_subscriber.store.node_count

        start_time = getattr(self.server, "_mf_start_time", None)
        uptime = int(time.time() - start_time) if start_time else None

        # Data age and staleness indicators (upstream improvement)
        data_age = None
        data_stale = False
        source_counts = {}
        if aggregator:
            data_age = aggregator.last_collect_age_seconds
            if data_age is not None:
                data_age = int(data_age)
                # Data older than 2x cache TTL is considered stale
                cache_ttl = (config.get("cache_ttl_minutes", 15) if config else 15) * 60
                data_stale = data_age > (cache_ttl * 2)
            source_counts = aggregator.last_collect_counts

        # Circuit breaker states for per-source health visibility
        circuit_breaker_states = {}
        if aggregator:
            circuit_breaker_states = aggregator.get_circuit_breaker_states()

        self._send_json({
            "status": "ok",
            "extension": "meshforge-maps",
            "version": "0.3.0-beta",
            "sources": config.get_enabled_sources() if config else [],
            "source_counts": source_counts,
            "mqtt_live": mqtt_status,
            "mqtt_node_count": mqtt_nodes,
            "uptime_seconds": uptime,
            "data_age_seconds": data_age,
            "data_stale": data_stale,
            "circuit_breakers": circuit_breaker_states,
        })

    def _send_json(self, data: Any, status: int = 200) -> None:
        try:
            body = json.dumps(data, default=str).encode("utf-8")
        except (TypeError, ValueError) as e:
            logger.error("JSON serialization error: %s", e)
            body = b'{"error": "serialization error"}'
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _find_map_file(self) -> Optional[Path]:
        """Locate the map HTML file."""
        web_dir = self._get_web_dir()
        if web_dir:
            p = Path(web_dir) / "meshforge_maps.html"
            if p.exists():
                return p
        # Fallback: relative to this source file
        return Path(__file__).parent.parent / "web" / "meshforge_maps.html"

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("HTTP %s", format % args)


class MapServer:
    """Manages the MeshForge Maps HTTP server lifecycle."""

    def __init__(self, config: MapsConfig):
        self._config = config
        self._aggregator = DataAggregator(config.to_dict())
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._port: int = 0  # Actual bound port
        self._web_dir = str(Path(__file__).parent.parent / "web")

    def start(self) -> bool:
        """Start the HTTP server in a background thread.

        Returns True if the server started successfully, False otherwise.
        Tries the configured port first, then falls back up to 4 adjacent ports.
        """
        base_port = self._config.get("http_port", 8808)
        last_error: Optional[Exception] = None

        for offset in range(5):
            port = base_port + offset
            try:
                self._server = HTTPServer(("127.0.0.1", port), MapRequestHandler)
                # Attach instance state to the server object so handlers can
                # access it via self.server without class-level mutation.
                self._server._mf_aggregator = self._aggregator  # type: ignore[attr-defined]
                self._server._mf_config = self._config  # type: ignore[attr-defined]
                self._server._mf_web_dir = self._web_dir  # type: ignore[attr-defined]
                self._server._mf_start_time = time.time()  # type: ignore[attr-defined]

                self._thread = threading.Thread(
                    target=self._server.serve_forever,
                    name="meshforge-maps-http",
                    daemon=True,
                )
                self._thread.start()
                self._port = port
                if offset > 0:
                    logger.warning(
                        "Port %d in use, MeshForge Maps started on http://127.0.0.1:%d",
                        base_port, port,
                    )
                else:
                    logger.info("MeshForge Maps server started on http://127.0.0.1:%d", port)
                return True
            except OSError as e:
                last_error = e
                logger.debug("Port %d unavailable: %s", port, e)
                continue

        logger.error(
            "Failed to start map server on ports %d-%d: %s",
            base_port, base_port + 4, last_error,
        )
        return False

    def stop(self) -> None:
        """Stop the HTTP server and clean up the aggregator."""
        if self._server:
            self._server.shutdown()
            logger.info("MeshForge Maps server stopped")
        self._aggregator.shutdown()
        self._server = None
        self._thread = None

    @property
    def port(self) -> int:
        """The actual port the server bound to (0 if not started)."""
        return self._port

    @property
    def aggregator(self) -> DataAggregator:
        return self._aggregator
