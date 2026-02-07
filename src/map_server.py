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
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .collectors.aggregator import DataAggregator
from .utils.config import NETWORK_COLORS, TILE_PROVIDERS, MapsConfig

logger = logging.getLogger(__name__)


class MapRequestHandler(SimpleHTTPRequestHandler):
    """HTTP request handler for MeshForge Maps."""

    # Set by the server
    aggregator: Optional[DataAggregator] = None
    config: Optional[MapsConfig] = None
    web_dir: Optional[str] = None

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
        if handler:
            handler()
        elif path.startswith("/api/nodes/"):
            # /api/nodes/<source_name>
            source = path.split("/")[-1]
            self._serve_source_geojson(source)
        else:
            # Serve static files from web directory
            if self.web_dir:
                self.directory = self.web_dir
            super().do_GET()

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
        if not self.aggregator:
            self._send_json({"error": "Aggregator not initialized"}, 503)
            return
        data = self.aggregator.collect_all()
        self._send_json(data)

    def _serve_source_geojson(self, source: str) -> None:
        """Serve GeoJSON from a single source."""
        if not self.aggregator:
            self._send_json({"error": "Aggregator not initialized"}, 503)
            return
        data = self.aggregator.collect_source(source)
        self._send_json(data)

    def _serve_config(self) -> None:
        """Serve current configuration (non-sensitive)."""
        if not self.config:
            self._send_json({})
            return
        cfg = self.config.to_dict()
        cfg["network_colors"] = NETWORK_COLORS
        self._send_json(cfg)

    def _serve_tile_providers(self) -> None:
        """Serve available tile provider definitions."""
        self._send_json(TILE_PROVIDERS)

    def _serve_sources(self) -> None:
        """Serve list of enabled data sources."""
        if not self.config:
            self._send_json({"sources": []})
            return
        self._send_json({
            "sources": self.config.get_enabled_sources(),
            "network_colors": NETWORK_COLORS,
        })

    def _serve_overlay(self) -> None:
        """Serve overlay data (space weather, terminator)."""
        if not self.aggregator:
            self._send_json({})
            return
        data = self.aggregator.collect_all()
        overlay = data.get("properties", {}).get("overlay_data", {})
        self._send_json(overlay)

    def _serve_topology(self) -> None:
        """Serve topology link data for D3.js force graph."""
        if not self.aggregator:
            self._send_json({"links": []})
            return
        links = self.aggregator.get_topology_links()
        self._send_json({"links": links, "link_count": len(links)})

    def _serve_status(self) -> None:
        """Serve server health status."""
        mqtt_status = "unavailable"
        if self.aggregator and self.aggregator._mqtt_subscriber:
            mqtt_status = "connected" if self.aggregator._mqtt_subscriber._running else "stopped"
        self._send_json({
            "status": "ok",
            "extension": "meshforge-maps",
            "version": "0.2.0-beta",
            "sources": self.config.get_enabled_sources() if self.config else [],
            "mqtt_live": mqtt_status,
        })

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _find_map_file(self) -> Optional[Path]:
        """Locate the map HTML file."""
        if self.web_dir:
            p = Path(self.web_dir) / "meshforge_maps.html"
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

        # Configure the handler class
        MapRequestHandler.aggregator = self._aggregator
        MapRequestHandler.config = config
        MapRequestHandler.web_dir = str(
            Path(__file__).parent.parent / "web"
        )

    def start(self) -> None:
        """Start the HTTP server in a background thread."""
        port = self._config.get("http_port", 8808)
        try:
            self._server = HTTPServer(("127.0.0.1", port), MapRequestHandler)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="meshforge-maps-http",
                daemon=True,
            )
            self._thread.start()
            logger.info("MeshForge Maps server started on http://127.0.0.1:%d", port)
        except OSError as e:
            logger.error("Failed to start map server on port %d: %s", port, e)

    def stop(self) -> None:
        """Stop the HTTP server."""
        if self._server:
            self._server.shutdown()
            logger.info("MeshForge Maps server stopped")
        self._server = None
        self._thread = None

    @property
    def aggregator(self) -> DataAggregator:
        return self._aggregator
