"""
MeshForge Maps - AREDN Data Collector

Collects node data from AREDN (Amateur Radio Emergency Data Network) mesh.
AREDN runs on repurposed WiFi hardware (Ubiquiti, MikroTik, TP-Link, GL.iNET)
using amateur radio frequencies under FCC Part 97.

Data source: AREDN sysinfo.json API (per-node, on-mesh)
  - Endpoint: http://<nodename>.local.mesh/a/sysinfo
  - Optional flags: ?hosts=1&services=1&lqm=1
  - Returns: node identity, location, system info, link quality

Each AREDN node exposes its own JSON API on the mesh network.
Nodes must be reachable on the local mesh for direct queries.

See: https://docs.arednmesh.org/en/latest/arednHow-toGuides/devtools.html
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import BaseCollector, make_feature, make_feature_collection

logger = logging.getLogger(__name__)

# MeshForge AREDN cache
AREDN_CACHE_PATH = Path.home() / ".local" / "share" / "meshforge" / "aredn_nodes.json"

# Default AREDN node discovery targets
DEFAULT_AREDN_NODES: List[str] = []


class AREDNCollector(BaseCollector):
    """Collects AREDN mesh node data via sysinfo.json API."""

    source_name = "aredn"

    def __init__(
        self,
        node_targets: Optional[List[str]] = None,
        cache_ttl_seconds: int = 900,
    ):
        super().__init__(cache_ttl_seconds)
        self._node_targets = node_targets or list(DEFAULT_AREDN_NODES)

    def _fetch(self) -> Dict[str, Any]:
        features: List[Dict[str, Any]] = []
        seen_ids: set = set()

        # Source 1: Direct AREDN node queries (if on mesh)
        for target in self._node_targets:
            node_features = self._fetch_from_node(target)
            for f in node_features:
                fid = f["properties"].get("id")
                if fid and fid not in seen_ids:
                    seen_ids.add(fid)
                    features.append(f)

        # Source 2: MeshForge AREDN cache
        cache_features = self._fetch_from_cache()
        for f in cache_features:
            fid = f["properties"].get("id")
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                features.append(f)

        # Source 3: MeshForge unified node cache (AREDN entries)
        unified = self._fetch_from_unified_cache()
        for f in unified:
            fid = f["properties"].get("id")
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                features.append(f)

        return make_feature_collection(features, self.source_name)

    def _fetch_from_node(self, target: str) -> List[Dict[str, Any]]:
        """Query a single AREDN node's sysinfo API with LQM data.

        Validates the HTTP response contains expected AREDN JSON fields
        (node, sysinfo, or meshrf) to confirm this is a real AREDN node
        and not some other HTTP service on the same port.
        """
        features = []
        url = f"http://{target}/a/sysinfo?lqm=1"
        try:
            req = Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "MeshForge/1.0",
            })
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())

            # Validate this is actually an AREDN node response
            if not isinstance(data, dict):
                logger.debug("AREDN node %s: response is not a JSON object", target)
                return features
            if not ("node" in data or "sysinfo" in data or "meshrf" in data):
                logger.debug("AREDN node %s: missing expected AREDN fields", target)
                return features

            # Parse the queried node itself
            feature = self._parse_sysinfo(data, target)
            if feature:
                features.append(feature)

            # Parse neighbor nodes from LQM data
            lqm = data.get("lqm", [])
            if isinstance(lqm, list):
                for neighbor in lqm:
                    nf = self._parse_lqm_neighbor(neighbor)
                    if nf:
                        features.append(nf)

            logger.debug("AREDN node %s returned %d entries", target, len(features))
        except (URLError, OSError, json.JSONDecodeError) as e:
            logger.debug("AREDN node %s unreachable: %s", target, e)
        return features

    def _parse_sysinfo(
        self, data: Dict[str, Any], target: str
    ) -> Optional[Dict[str, Any]]:
        """Parse AREDN sysinfo.json response into a GeoJSON feature."""
        lat = data.get("lat")
        lon = data.get("lon")
        if lat is None or lon is None:
            return None
        try:
            lat = float(lat)
            lon = float(lon)
        except (ValueError, TypeError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None

        node_name = data.get("node", target)
        model = data.get("model", "")
        firmware = data.get("firmware_version", "")
        api_version = data.get("api_version", "")

        # System metrics (with empty-sequence guards)
        sysinfo = data.get("sysinfo", {}) or {}
        uptime = sysinfo.get("uptime", "")
        loads = sysinfo.get("loads", []) or []

        return make_feature(
            node_id=node_name,
            lat=lat,
            lon=lon,
            network="aredn",
            name=node_name,
            node_type="aredn_node",
            hardware=model,
            firmware=firmware,
            api_version=api_version,
            uptime=uptime,
            load_avg=loads[0] if loads else None,
            is_online=True,
            grid_square=data.get("grid_square", ""),
            description=f"AREDN {model} - {firmware}",
        )

    def _parse_lqm_neighbor(self, neighbor: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse an LQM neighbor entry. Note: neighbors may lack coordinates."""
        # LQM entries typically don't include lat/lon directly
        # They contain link quality metrics for topology visualization
        name = neighbor.get("name", "")
        if not name:
            return None

        # Store as a feature without geometry for topology data
        # (will be resolved to coordinates if the neighbor is also queried)
        return None  # Skip for now - no coordinates available in LQM

    def _fetch_from_cache(self) -> List[Dict[str, Any]]:
        """Read MeshForge's AREDN node cache."""
        features = []
        if not AREDN_CACHE_PATH.exists():
            return features
        try:
            with open(AREDN_CACHE_PATH, "r") as f:
                data = json.load(f)

            if data.get("type") == "FeatureCollection":
                features = [
                    f
                    for f in data.get("features", [])
                    if f.get("properties", {}).get("network") == "aredn"
                ]
            logger.debug("AREDN cache returned %d nodes", len(features))
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("AREDN cache read failed: %s", e)
        return features

    def _fetch_from_unified_cache(self) -> List[Dict[str, Any]]:
        """Read AREDN nodes from MeshForge's unified node cache."""
        features = []
        unified_path = Path.home() / ".local" / "share" / "meshforge" / "node_cache.json"
        if not unified_path.exists():
            return features
        try:
            with open(unified_path, "r") as f:
                data = json.load(f)
            if data.get("type") == "FeatureCollection":
                features = [
                    f
                    for f in data.get("features", [])
                    if f.get("properties", {}).get("network") == "aredn"
                ]
            logger.debug("Unified cache returned %d AREDN nodes", len(features))
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("Unified cache read failed: %s", e)
        return features
