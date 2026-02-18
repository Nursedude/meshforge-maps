"""HTTP data client for fetching MapServer state.

Connects to the running MeshForge Maps HTTP API to retrieve node data,
health scores, alerts, topology, and propagation info for TUI display.
All requests use urllib (stdlib) with short timeouts to keep the TUI responsive.
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 3  # seconds


class MapDataClient:
    """Lightweight HTTP client for the MeshForge Maps REST API."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8808):
        self._base = f"http://{host}:{port}"

    @property
    def base_url(self) -> str:
        return self._base

    def _get(self, path: str) -> Optional[Dict[str, Any]]:
        """Fetch JSON from an API endpoint. Returns None on failure."""
        url = f"{self._base}{path}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
            logger.debug("API fetch failed %s: %s", path, e)
            return None

    # -- High-level data accessors --

    def server_status(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/status")

    def health_check(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/health")

    def nodes_geojson(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/nodes/geojson")

    def node_health_summary(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/node-health/summary")

    def all_node_health(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/node-health")

    def node_states_summary(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/node-states/summary")

    def all_node_states(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/node-states")

    def alerts(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/alerts")

    def active_alerts(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/alerts/active")

    def alert_summary(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/alerts/summary")

    def alert_rules(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/alerts/rules")

    def topology(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/topology")

    def sources(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/sources")

    def hamclock(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/hamclock")

    def perf_stats(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/perf")

    def analytics_summary(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/analytics/summary")

    def config_drift(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/config-drift")

    def mqtt_stats(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/mqtt/stats")

    # -- Per-node detail accessors --

    def node_health(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Fetch detailed health breakdown for a single node."""
        return self._get(f"/api/nodes/{node_id}/health")

    def node_history(self, node_id: str, limit: int = 50) -> Optional[Dict[str, Any]]:
        """Fetch observation history for a single node."""
        return self._get(f"/api/nodes/{node_id}/history?limit={limit}")

    def node_alerts(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Fetch alerts for a specific node."""
        return self._get(f"/api/alerts?node_id={node_id}")

    def topology_geojson(self) -> Optional[Dict[str, Any]]:
        """Fetch topology as GeoJSON with link quality data."""
        return self._get("/api/topology/geojson")

    def is_alive(self) -> bool:
        """Quick liveness check."""
        result = self.health_check()
        return result is not None
