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

from src.collectors.base import bounded_read

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 3  # seconds

# Aggregate GeoJSON across all sources can rival the largest single upstream
# (meshcore ~31 MB at ~40K nodes). 64 MB matches MESHCORE_MAX_RESPONSE_BYTES
# and bounds memory if the TUI ever points at a hostile/buggy remote MapServer
# (cloud deploy, post-May 17 demo).
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


def _load_api_key() -> Optional[str]:
    """Read the admin key from the same config the SERVER reads it from.

    Deliberately MapsConfig rather than a hand-rolled file read: the server
    resolves api_key through DEFAULT_CONFIG < global.ini < settings.json, and a
    client that parsed only settings.json would disagree with the server on any
    box that set the key in global.ini -- a checker consuming a different
    artifact than its subject. Returns None when no key is configured, which is
    the default; the client then sends no header and behaves as it always has.
    """
    try:
        from src.utils.config import MapsConfig

        key = MapsConfig().get("api_key")
        return str(key) if key else None
    except Exception as e:  # config unreadable -> unauthenticated, not fatal
        logger.debug("No maps api_key available for TUI requests: %s", e)
        return None


class MapDataClient:
    """Lightweight HTTP client for the MeshForge Maps REST API."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8808,
        scheme: str = "http",
        api_key: Optional[str] = None,
    ):
        self._base = f"{scheme}://{host}:{port}"
        # Introspective endpoints (/api/dependencies, /api/config-drift,
        # /api/perf, ...) require the admin key since the read-surface tiering.
        # Load it once here rather than per request: _get() runs on the TUI's
        # 5-second refresh loop and must not touch disk on every tab.
        self._api_key = api_key if api_key is not None else _load_api_key()

    @property
    def base_url(self) -> str:
        return self._base

    def _get(self, path: str) -> Optional[Dict[str, Any]]:
        """Fetch JSON from an API endpoint. Returns None on failure."""
        url = f"{self._base}{path}"
        try:
            headers = {"Accept": "application/json"}
            if self._api_key:
                headers["X-MeshForge-Key"] = self._api_key
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                return json.loads(bounded_read(resp, max_bytes=MAX_RESPONSE_BYTES).decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                logger.warning(
                    "API fetch refused %s: 401 -- this endpoint needs the admin "
                    "key. Set api_key in the maps settings.json this TUI reads "
                    "(or re-run the setup wizard); the server has one configured.",
                    path,
                )
            else:
                logger.warning("API fetch failed %s: %s", path, e)
            return None
        except (urllib.error.URLError, OSError, ValueError) as e:
            logger.warning("API fetch failed %s: %s", path, e)
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

    def dependencies_info(self) -> Optional[Dict[str, Any]]:
        """Fetch dependency version information for the System tab."""
        return self._get("/api/dependencies")

    def is_alive(self) -> bool:
        """Quick liveness check."""
        result = self.health_check()
        return result is not None
