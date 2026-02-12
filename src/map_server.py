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
import re
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from . import __version__
from .collectors.aggregator import DataAggregator
from .utils.config import NETWORK_COLORS, TILE_PROVIDERS, MapsConfig
from .utils.config_drift import ConfigDriftDetector
from .utils.event_bus import Event, EventBus, EventType, NodeEvent
from .utils.health_scoring import NodeHealthScorer
from .utils.meshtastic_api_proxy import MeshtasticApiProxy
from .utils.node_history import NodeHistoryDB
from .utils.node_state import NodeState, NodeStateTracker
from .utils.shared_health_state import SharedHealthStateReader
from .utils.websocket_server import MapWebSocketServer

logger = logging.getLogger(__name__)

# Node IDs must be hex strings, optionally prefixed with '!'
# e.g. "!a1b2c3d4" or "a1b2c3d4" — up to 16 hex chars
_NODE_ID_RE = re.compile(r"^!?[0-9a-fA-F]{1,16}$")


def _safe_query_param(query: Dict[str, List[str]], key: str,
                      default: Optional[str] = None) -> Optional[str]:
    """Safely extract a single query parameter value.

    Returns the first value for the key, or default if missing/empty.
    """
    values = query.get(key)
    if not values:
        return default
    return values[0] if values[0] else default


def _validate_node_id(node_id: str) -> bool:
    """Validate that a node ID looks like a valid Meshtastic hex ID."""
    return bool(_NODE_ID_RE.match(node_id))


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

    def _get_node_history(self) -> Optional[NodeHistoryDB]:
        return getattr(self.server, "_mf_node_history", None)

    def _get_shared_health(self) -> Optional[SharedHealthStateReader]:
        return getattr(self.server, "_mf_shared_health", None)

    def _get_config_drift(self) -> Optional[ConfigDriftDetector]:
        return getattr(self.server, "_mf_config_drift", None)

    def _get_node_state(self) -> Optional[NodeStateTracker]:
        return getattr(self.server, "_mf_node_state", None)

    def _get_health_scorer(self) -> Optional[NodeHealthScorer]:
        return getattr(self.server, "_mf_health_scorer", None)

    # Route name -> method name mapping (built once, not per request)
    _ROUTE_TABLE = {
        "": "_serve_map",
        "/index.html": "_serve_map",
        "/api/nodes/geojson": "_serve_geojson",
        "/api/nodes/all": "_serve_geojson",
        "/api/config": "_serve_config",
        "/api/tile-providers": "_serve_tile_providers",
        "/api/sources": "_serve_sources",
        "/api/overlay": "_serve_overlay",
        "/api/topology": "_serve_topology",
        "/api/topology/geojson": "_serve_topology_geojson",
        "/api/status": "_serve_status",
        "/api/health": "_serve_health",
        "/api/hamclock": "_serve_hamclock",
        "/api/core-health": "_serve_core_health",
        "/api/mqtt/stats": "_serve_mqtt_stats",
        "/api/history/nodes": "_serve_tracked_nodes",
        "/api/config-drift": "_serve_config_drift",
        "/api/config-drift/summary": "_serve_config_drift_summary",
        "/api/node-states": "_serve_node_states",
        "/api/node-states/summary": "_serve_node_states_summary",
        "/api/proxy/stats": "_serve_proxy_stats",
        "/api/node-health": "_serve_all_node_health",
        "/api/node-health/summary": "_serve_node_health_summary",
        "/api/perf": "_serve_perf_stats",
    }

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        method_name = self._ROUTE_TABLE.get(path)
        handler = getattr(self, method_name) if method_name else None
        try:
            if handler:
                handler()
            elif path.startswith("/api/nodes/") and path.endswith("/trajectory"):
                # /api/nodes/<node_id>/trajectory
                parts = path.split("/")
                if len(parts) >= 4:
                    node_id = parts[3]
                    if not _validate_node_id(node_id):
                        self._send_json({"error": "Invalid node ID format"}, 400)
                    else:
                        self._serve_trajectory(node_id, query)
                else:
                    self._send_json({"error": "Not found"}, 404)
            elif path.startswith("/api/nodes/") and path.endswith("/health"):
                # /api/nodes/<node_id>/health
                parts = path.split("/")
                if len(parts) >= 4:
                    node_id = parts[3]
                    if not _validate_node_id(node_id):
                        self._send_json({"error": "Invalid node ID format"}, 400)
                    else:
                        self._serve_node_health(node_id)
                else:
                    self._send_json({"error": "Not found"}, 404)
            elif path.startswith("/api/nodes/") and path.endswith("/history"):
                # /api/nodes/<node_id>/history
                parts = path.split("/")
                if len(parts) >= 4:
                    node_id = parts[3]
                    if not _validate_node_id(node_id):
                        self._send_json({"error": "Invalid node ID format"}, 400)
                    else:
                        self._serve_node_history(node_id, query)
                else:
                    self._send_json({"error": "Not found"}, 404)
            elif path.startswith("/api/snapshot/"):
                # /api/snapshot/<timestamp>
                parts = path.split("/")
                if len(parts) >= 3:
                    try:
                        ts = int(parts[-1])
                        self._serve_snapshot(ts)
                    except (ValueError, IndexError):
                        self._send_json({"error": "Invalid timestamp"}, 400)
                else:
                    self._send_json({"error": "Not found"}, 404)
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

    def _get_cors_origin(self) -> Optional[str]:
        """Get configured CORS origin, or None for same-origin (no CORS headers)."""
        config = self._get_config()
        if config:
            return config.get("cors_allowed_origin")
        return None

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
        # Include WebSocket port so frontend can connect
        ws_server = getattr(self.server, "_mf_ws_server", None)
        if ws_server:
            cfg["ws_port"] = ws_server.port
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

    def _serve_hamclock(self) -> None:
        """Serve HamClock-specific data (propagation, bands, DE/DX, spots).

        Returns all available HamClock data from the collector directly,
        using cached data if fresh.
        """
        aggregator = self._get_aggregator()
        if not aggregator:
            self._send_json({"error": "Aggregator not initialized"}, 503)
            return
        hamclock_collector = aggregator.get_collector("hamclock")
        if not hamclock_collector:
            self._send_json({"error": "HamClock source not enabled", "available": False}, 404)
            return
        data = hamclock_collector.get_hamclock_data()
        self._send_json(data)

    def _serve_topology_geojson(self) -> None:
        """Serve topology as GeoJSON FeatureCollection with SNR-colored edges.

        Each link is a GeoJSON LineString Feature with properties including
        SNR value, quality tier label, and hex color for direct rendering.
        """
        aggregator = self._get_aggregator()
        if not aggregator:
            self._send_json({"type": "FeatureCollection", "features": []})
            return
        self._send_json(aggregator.get_topology_geojson())

    def _serve_trajectory(self, node_id: str, query: Dict) -> None:
        """Serve node trajectory as GeoJSON LineString."""
        history = self._get_node_history()
        if not history:
            self._send_json({"error": "Node history not available"}, 503)
            return

        since_str = _safe_query_param(query, "since")
        until_str = _safe_query_param(query, "until")
        try:
            since = int(since_str) if since_str else None
            until = int(until_str) if until_str else None
        except (ValueError, TypeError):
            self._send_json({"error": "Invalid since/until parameter"}, 400)
            return

        data = history.get_trajectory_geojson(node_id, since=since, until=until)
        self._send_json(data)

    def _serve_node_history(self, node_id: str, query: Dict) -> None:
        """Serve node observation history as JSON list."""
        history = self._get_node_history()
        if not history:
            self._send_json({"error": "Node history not available"}, 503)
            return

        since_str = _safe_query_param(query, "since")
        limit_str = _safe_query_param(query, "limit", "100")
        try:
            since = int(since_str) if since_str else None
            limit = int(limit_str) if limit_str else 100
            limit = max(1, min(limit, 10000))  # Clamp to reasonable range
        except (ValueError, TypeError):
            self._send_json({"error": "Invalid since/limit parameter"}, 400)
            return

        observations = history.get_node_history(node_id, since=since, limit=limit)
        self._send_json({
            "node_id": node_id,
            "observations": observations,
            "count": len(observations),
        })

    def _serve_snapshot(self, timestamp: int) -> None:
        """Serve historical network snapshot at a point in time."""
        history = self._get_node_history()
        if not history:
            self._send_json({"error": "Node history not available"}, 503)
            return
        self._send_json(history.get_snapshot(timestamp))

    def _serve_tracked_nodes(self) -> None:
        """Serve list of all tracked nodes with observation counts."""
        history = self._get_node_history()
        if not history:
            self._send_json({"error": "Node history not available"}, 503)
            return
        nodes = history.get_tracked_nodes()
        self._send_json({
            "nodes": nodes,
            "total_nodes": len(nodes),
            "total_observations": history.observation_count,
        })

    def _serve_core_health(self) -> None:
        """Serve MeshForge core shared health state.

        Reads the cross-process health database written by MeshForge core
        (gateway bridge status, service states, latency percentiles).
        Returns empty/unavailable when core is not running.
        """
        reader = self._get_shared_health()
        if not reader:
            self._send_json({"available": False, "services": []})
            return
        # Attempt refresh if not available (core may have started since maps)
        if not reader.available:
            reader.refresh()
        self._send_json(reader.get_summary())

    def _serve_mqtt_stats(self) -> None:
        """Serve MQTT subscriber statistics (upstream: monitoring integration).

        Returns broker connection state, message counts, and node store stats.
        """
        aggregator = self._get_aggregator()
        if not aggregator or not aggregator.mqtt_subscriber:
            self._send_json({"available": False, "status": "not_configured"})
            return
        self._send_json(aggregator.mqtt_subscriber.get_stats())

    def _serve_health(self) -> None:
        """Serve composite health score (0-100) with per-source breakdown.

        Scoring factors:
        - Data freshness: 40 points (full if <cache_ttl, degrades to 0 at 3x TTL)
        - Source availability: 30 points (proportional to sources with data)
        - Circuit breaker health: 30 points (proportional to CLOSED breakers)
        """
        aggregator = self._get_aggregator()
        config = self._get_config()

        if not aggregator:
            self._send_json({
                "score": 0,
                "status": "offline",
                "components": {},
            })
            return

        cache_ttl = (config.get("cache_ttl_minutes", 15) if config else 15) * 60

        # Freshness score (0-40): how recent is the data?
        freshness_score = 0.0
        data_age = aggregator.last_collect_age_seconds
        if data_age is not None:
            if data_age <= cache_ttl:
                freshness_score = 40.0
            elif data_age <= cache_ttl * 3:
                freshness_score = 40.0 * (1.0 - (data_age - cache_ttl) / (cache_ttl * 2))
            # else: 0

        # Source availability score (0-30): how many sources returned data?
        source_score = 0.0
        source_counts = aggregator.last_collect_counts
        enabled_count = aggregator.enabled_collector_count
        if enabled_count > 0:
            sources_with_data = sum(1 for c in source_counts.values() if c > 0)
            source_score = 30.0 * (sources_with_data / enabled_count)

        # Circuit breaker score (0-30): how many breakers are CLOSED (healthy)?
        cb_score = 0.0
        cb_states = aggregator.get_circuit_breaker_states()
        if cb_states:
            closed_count = sum(
                1 for s in cb_states.values() if s.get("state") == "closed"
            )
            cb_score = 30.0 * (closed_count / len(cb_states))

        total_score = int(freshness_score + source_score + cb_score)
        total_score = max(0, min(100, total_score))

        # Map score to status string
        if total_score >= 80:
            status = "healthy"
        elif total_score >= 60:
            status = "fair"
        elif total_score >= 30:
            status = "degraded"
        else:
            status = "critical"

        self._send_json({
            "score": total_score,
            "status": status,
            "components": {
                "freshness": {"score": round(freshness_score, 1), "max": 40},
                "sources": {"score": round(source_score, 1), "max": 30},
                "circuit_breakers": {"score": round(cb_score, 1), "max": 30},
            },
            "data_age_seconds": int(data_age) if data_age is not None else None,
            "sources_reporting": source_counts,
        })

    def _serve_config_drift(self) -> None:
        """Serve config drift events for all nodes."""
        detector = self._get_config_drift()
        if not detector:
            self._send_json({"error": "Config drift detection not available"}, 503)
            return
        self._send_json(detector.get_summary())

    def _serve_config_drift_summary(self) -> None:
        """Serve config drift summary."""
        detector = self._get_config_drift()
        if not detector:
            self._send_json({"error": "Config drift detection not available"}, 503)
            return
        self._send_json(detector.get_summary())

    def _serve_node_states(self) -> None:
        """Serve all node connectivity states."""
        tracker = self._get_node_state()
        if not tracker:
            self._send_json({"error": "Node state tracking not available"}, 503)
            return
        self._send_json({
            "states": tracker.get_all_states(),
            "summary": tracker.get_summary(),
        })

    def _serve_node_states_summary(self) -> None:
        """Serve node state summary (counts by state)."""
        tracker = self._get_node_state()
        if not tracker:
            self._send_json({"error": "Node state tracking not available"}, 503)
            return
        self._send_json(tracker.get_summary())

    def _serve_proxy_stats(self) -> None:
        """Serve Meshtastic API proxy statistics."""
        proxy = getattr(self.server, "_mf_proxy", None)
        if not proxy:
            self._send_json({"available": False, "status": "not_configured"})
            return
        self._send_json(proxy.stats)

    def _serve_node_health(self, node_id: str) -> None:
        """Serve health score for a single node."""
        scorer = self._get_health_scorer()
        if not scorer:
            self._send_json({"error": "Health scoring not available"}, 503)
            return

        # Try cached score first
        cached = scorer.get_node_score(node_id)
        if cached:
            self._send_json(cached)
            return

        # Score on demand from current GeoJSON data
        aggregator = self._get_aggregator()
        if not aggregator:
            self._send_json({"error": "No data available"}, 503)
            return

        data = aggregator.collect_all()
        features = data.get("features", [])
        for f in features:
            props = f.get("properties", {})
            if props.get("id") == node_id:
                tracker = self._get_node_state()
                conn_state = None
                if tracker:
                    state = tracker.get_node_state(node_id)
                    conn_state = state.value if state else None
                result = scorer.score_node(node_id, props, conn_state)
                self._send_json(result.to_dict())
                return

        self._send_json({"error": "Node not found"}, 404)

    def _serve_all_node_health(self) -> None:
        """Serve health scores for all nodes."""
        scorer = self._get_health_scorer()
        if not scorer:
            self._send_json({"error": "Health scoring not available"}, 503)
            return

        # Score all current nodes
        aggregator = self._get_aggregator()
        if not aggregator:
            self._send_json({"error": "No data available"}, 503)
            return

        data = aggregator.collect_all()
        features = data.get("features", [])
        tracker = self._get_node_state()

        results = []
        for f in features:
            props = f.get("properties", {})
            node_id = props.get("id")
            if not node_id:
                continue
            conn_state = None
            if tracker:
                state = tracker.get_node_state(node_id)
                conn_state = state.value if state else None
            result = scorer.score_node(node_id, props, conn_state)
            results.append(result.to_dict())

        self._send_json({
            "nodes": results,
            "count": len(results),
        })

    def _serve_node_health_summary(self) -> None:
        """Serve health score summary statistics."""
        scorer = self._get_health_scorer()
        if not scorer:
            self._send_json({"error": "Health scoring not available"}, 503)
            return
        self._send_json(scorer.get_summary())

    def _serve_perf_stats(self) -> None:
        """Serve performance profiling statistics."""
        aggregator = self._get_aggregator()
        if not aggregator:
            self._send_json({"error": "Aggregator not available"}, 503)
            return
        self._send_json(aggregator.perf_monitor.get_stats())

    def _serve_status(self) -> None:
        """Serve server health status with uptime, data age, and node store stats."""
        aggregator = self._get_aggregator()
        config = self._get_config()
        mqtt_status = "unavailable"
        mqtt_nodes = 0
        if aggregator and aggregator.mqtt_subscriber:
            mqtt_status = "connected" if aggregator.mqtt_subscriber._running.is_set() else "stopped"
            mqtt_nodes = aggregator.mqtt_subscriber.store.node_count

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

        # WebSocket server stats
        ws_server = getattr(self.server, "_mf_ws_server", None)
        websocket_stats = ws_server.stats if ws_server else None

        # Event bus stats
        event_bus_stats = None
        if aggregator:
            event_bus_stats = aggregator.event_bus.stats

        # Per-source health (last error, success counts, etc.)
        source_health = {}
        if aggregator:
            source_health = aggregator.get_source_health()

        self._send_json({
            "status": "ok",
            "extension": "meshforge-maps",
            "version": __version__,
            "sources": config.get_enabled_sources() if config else [],
            "source_counts": source_counts,
            "source_health": source_health,
            "mqtt_live": mqtt_status,
            "mqtt_node_count": mqtt_nodes,
            "uptime_seconds": uptime,
            "data_age_seconds": data_age,
            "data_stale": data_stale,
            "circuit_breakers": circuit_breaker_states,
            "websocket": websocket_stats,
            "event_bus": event_bus_stats,
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
        cors_origin = self._get_cors_origin()
        if cors_origin:
            self.send_header("Access-Control-Allow-Origin", cors_origin)
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

        # WebSocket broadcast server for real-time updates
        self._ws_server: Optional[MapWebSocketServer] = None
        self._ws_port: int = 0

        # Node history DB for trajectory tracking
        self._node_history: Optional[NodeHistoryDB] = None
        try:
            self._node_history = NodeHistoryDB()
        except Exception as e:
            logger.warning("Node history DB not available: %s", e)

        # Shared health state reader for cross-process visibility
        self._shared_health: Optional[SharedHealthStateReader] = None
        try:
            self._shared_health = SharedHealthStateReader()
        except Exception as e:
            logger.debug("Shared health state not available: %s", e)

        # Config drift detection
        self._config_drift = ConfigDriftDetector()

        # Node connectivity state machine
        self._node_state = NodeStateTracker()

        # Per-node health scoring
        self._health_scorer = NodeHealthScorer()

        # Wire node eviction cleanup to drift detector and state tracker
        if self._aggregator.mqtt_subscriber:
            self._aggregator.mqtt_subscriber.store._on_node_removed = (
                self._handle_node_removed
            )

        # Meshtastic API proxy (serves meshtasticd-compatible JSON endpoints)
        self._proxy: Optional[MeshtasticApiProxy] = None
        if config.get("enable_meshtastic", True):
            mqtt_store = None
            if self._aggregator.mqtt_subscriber:
                mqtt_store = self._aggregator.mqtt_subscriber.store
            self._proxy = MeshtasticApiProxy(
                mqtt_store=mqtt_store,
                port=config.get("meshtastic_proxy_port", 4404),
            )

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
                self._server._mf_node_history = self._node_history  # type: ignore[attr-defined]
                self._server._mf_shared_health = self._shared_health  # type: ignore[attr-defined]
                self._server._mf_config_drift = self._config_drift  # type: ignore[attr-defined]
                self._server._mf_node_state = self._node_state  # type: ignore[attr-defined]
                self._server._mf_health_scorer = self._health_scorer  # type: ignore[attr-defined]
                if self._proxy:
                    self._server._mf_proxy = self._proxy  # type: ignore[attr-defined]

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

                # Subscribe node history recording to position events
                if self._node_history:
                    self._aggregator.event_bus.subscribe(
                        EventType.NODE_POSITION, self._record_node_position,
                    )

                # Subscribe config drift and node state to info/telemetry events
                self._aggregator.event_bus.subscribe(
                    EventType.NODE_INFO, self._handle_node_info_for_drift,
                )
                self._aggregator.event_bus.subscribe(
                    EventType.NODE_POSITION, self._handle_heartbeat,
                )
                self._aggregator.event_bus.subscribe(
                    EventType.NODE_TELEMETRY, self._handle_heartbeat,
                )
                self._aggregator.event_bus.subscribe(
                    EventType.NODE_INFO, self._handle_heartbeat,
                )

                # Start Meshtastic API proxy
                if self._proxy:
                    self._proxy.start()

                # Start WebSocket server on adjacent port
                self._start_websocket(port + 1)
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

    def _handle_node_info_for_drift(self, event: Event) -> None:
        """Feed node info events to config drift detector."""
        if not isinstance(event, NodeEvent):
            return
        data = event.data or {}
        self._config_drift.check_node(
            event.node_id,
            **{k: v for k, v in data.items() if v is not None},
        )

    def _handle_node_removed(self, node_id: str) -> None:
        """Clean up drift, state, and health tracking when a node is evicted."""
        self._config_drift.remove_node(node_id)
        self._node_state.remove_node(node_id)
        self._health_scorer.remove_node(node_id)

    def _handle_heartbeat(self, event: Event) -> None:
        """Feed any node event as a heartbeat to the state tracker."""
        if not isinstance(event, NodeEvent):
            return
        self._node_state.record_heartbeat(event.node_id)

    def _start_websocket(self, ws_port: int) -> None:
        """Start the WebSocket server and wire it to the event bus.

        Tries the given port first, then falls back up to 4 adjacent ports
        (matching the HTTP server fallback pattern).
        """
        for offset in range(5):
            port = ws_port + offset
            self._ws_server = MapWebSocketServer(
                host="127.0.0.1",
                port=port,
                history_size=50,
            )
            if self._ws_server.start():
                self._ws_port = port
                if offset > 0:
                    logger.warning(
                        "WebSocket port %d in use, started on ws://127.0.0.1:%d",
                        ws_port, port,
                    )
                # Subscribe to all node events and forward to WebSocket clients
                self._aggregator.event_bus.subscribe(
                    None, self._forward_to_websocket,
                )
                # Attach WS server ref so status handler can report stats
                if self._server:
                    self._server._mf_ws_server = self._ws_server  # type: ignore[attr-defined]
                return
        # All ports failed
        logger.info("WebSocket server not started (optional dependency or ports unavailable)")
        self._ws_server = None

    def _record_node_position(self, event: Event) -> None:
        """Record node position to history DB when position events arrive."""
        if not self._node_history:
            return
        if not isinstance(event, NodeEvent):
            return
        if event.lat is None or event.lon is None:
            return
        self._node_history.record_observation(
            node_id=event.node_id,
            lat=event.lat,
            lon=event.lon,
            network=event.source or "mqtt",
        )

    def _forward_to_websocket(self, event: Event) -> None:
        """Bridge an event bus event to WebSocket broadcast."""
        if not self._ws_server:
            return
        msg: Dict[str, Any] = {
            "type": event.event_type.value,
            "timestamp": event.timestamp,
            "source": event.source,
        }
        if isinstance(event, NodeEvent):
            msg["node_id"] = event.node_id
            if event.lat is not None:
                msg["lat"] = event.lat
            if event.lon is not None:
                msg["lon"] = event.lon
        if event.data:
            msg["data"] = event.data
        self._ws_server.broadcast(msg)

    def stop(self) -> None:
        """Stop the HTTP server, WebSocket server, and clean up all resources."""
        if self._proxy:
            self._proxy.stop()
            self._proxy = None
        if self._ws_server:
            self._ws_server.shutdown()
            self._ws_server = None
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            logger.info("MeshForge Maps server stopped")
        # Wait for the HTTP server thread to fully exit before releasing
        # the port, preventing "Address already in use" on rapid restart
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("HTTP server thread did not exit within 5s")
        self._aggregator.shutdown()
        if self._node_history:
            self._node_history.close()
            self._node_history = None
        if self._shared_health:
            self._shared_health.close()
            self._shared_health = None
        self._server = None
        self._thread = None

    @property
    def port(self) -> int:
        """The actual port the server bound to (0 if not started)."""
        return self._port

    @property
    def ws_port(self) -> int:
        """The WebSocket server port (0 if not started)."""
        return self._ws_port

    @property
    def aggregator(self) -> DataAggregator:
        return self._aggregator

    @property
    def node_history(self) -> Optional[NodeHistoryDB]:
        return self._node_history
