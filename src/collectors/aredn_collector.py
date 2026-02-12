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
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import BaseCollector, make_feature, make_feature_collection, validate_coordinates

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
        self._topo_lock = threading.Lock()
        # Topology links from LQM data (source_name -> [{neighbor, snr, quality, ...}])
        self._lqm_links: List[Dict[str, Any]] = []
        # Known node coordinates for resolving LQM neighbor positions
        self._node_coords: Dict[str, tuple] = {}  # node_name -> (lat, lon)

    def _fetch(self) -> Dict[str, Any]:
        features: List[Dict[str, Any]] = []
        seen_ids: set = set()
        lqm_links: List[Dict[str, Any]] = []

        # Source 1: Direct AREDN node queries (if on mesh)
        for target in self._node_targets:
            node_features, links = self._fetch_from_node(target)
            lqm_links.extend(links)
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

        # Build coordinate lookup for all known nodes
        node_coords: Dict[str, tuple] = {}
        for f in features:
            props = f.get("properties", {})
            geom = f.get("geometry", {})
            coords = geom.get("coordinates", [])
            fid = props.get("id")
            if fid and len(coords) >= 2:
                node_coords[fid] = (coords[1], coords[0])  # lat, lon

        # Swap topology data under lock for thread safety
        with self._topo_lock:
            self._lqm_links = lqm_links
            self._node_coords = node_coords

        return make_feature_collection(features, self.source_name)

    def _fetch_from_node(self, target: str) -> tuple:
        """Query a single AREDN node's sysinfo API with LQM data.

        Validates the HTTP response contains expected AREDN JSON fields
        (node, sysinfo, or meshrf) to confirm this is a real AREDN node
        and not some other HTTP service on the same port.

        Returns (features, lqm_links) tuple.
        """
        features: List[Dict[str, Any]] = []
        links: List[Dict[str, Any]] = []
        # AREDN API runs on port 8080 (not port 80)
        # Use explicit port check to avoid IPv6 false positives
        host = target if ":" in target and not target.startswith("[") else f"{target}:8080"
        url = f"http://{host}/a/sysinfo?lqm=1"
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
                return features, links
            if not ("node" in data or "sysinfo" in data or "meshrf" in data):
                logger.debug("AREDN node %s: missing expected AREDN fields", target)
                return features, links

            # Parse the queried node itself
            feature = self._parse_sysinfo(data, target)
            if feature:
                features.append(feature)

            # Parse LQM neighbor entries for topology links
            node_name = data.get("node", target)
            lqm = data.get("lqm", [])
            if isinstance(lqm, list):
                for neighbor in lqm:
                    link = self._parse_lqm_neighbor(neighbor, node_name)
                    if link:
                        links.append(link)

            logger.debug("AREDN node %s returned %d entries", target, len(features))
        except (URLError, OSError, json.JSONDecodeError) as e:
            logger.debug("AREDN node %s unreachable: %s", target, e)
        return features, links

    def _parse_sysinfo(
        self, data: Dict[str, Any], target: str
    ) -> Optional[Dict[str, Any]]:
        """Parse AREDN sysinfo.json response into a GeoJSON feature."""
        coords = validate_coordinates(data.get("lat"), data.get("lon"))
        if coords is None:
            return None
        lat, lon = coords

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

    def _parse_lqm_neighbor(
        self, neighbor: Dict[str, Any], source_node: str
    ) -> Optional[Dict[str, Any]]:
        """Parse an LQM neighbor entry into a topology link.

        AREDN LQM entries contain link quality metrics between nodes:
          - name: neighbor node hostname
          - snr: signal-to-noise ratio (dB)
          - noise: noise floor (dBm)
          - tx_quality: transmit quality percentage (0-100)
          - rx_quality: receive quality percentage (0-100)
          - quality: overall link quality percentage
          - type: link type (RF, DTD, TUN, etc.)
          - blocked: whether the link is blocked by LQM

        Returns a topology link dict suitable for visualization,
        or None if the entry is invalid or blocked.
        """
        name = neighbor.get("name", "")
        if not name:
            return None

        # Skip blocked links (LQM has decided this link is unusable)
        if neighbor.get("blocked"):
            return None

        # Extract link quality metrics
        snr = neighbor.get("snr")
        noise = neighbor.get("noise")
        quality = neighbor.get("quality")
        tx_quality = neighbor.get("tx_quality")
        rx_quality = neighbor.get("rx_quality")
        link_type = neighbor.get("type", "")

        # Validate SNR if present
        if snr is not None:
            try:
                snr = float(snr)
            except (ValueError, TypeError):
                snr = None

        # Validate quality if present
        if quality is not None:
            try:
                quality = int(quality)
                if not (0 <= quality <= 100):
                    quality = None
            except (ValueError, TypeError):
                quality = None

        link = {
            "source": source_node,
            "target": name,
            "snr": snr,
            "noise": noise,
            "quality": quality,
            "tx_quality": tx_quality,
            "rx_quality": rx_quality,
            "link_type": link_type,
            "network": "aredn",
        }
        # Strip None values
        return {k: v for k, v in link.items() if v is not None}

    def get_topology_links(self) -> List[Dict[str, Any]]:
        """Return AREDN topology links with coordinates resolved.

        Enriches LQM link data with source/target coordinates from
        known node positions. Links where both endpoints have known
        coordinates are returned with full positioning data.
        """
        with self._topo_lock:
            lqm_links = list(self._lqm_links)
            node_coords = dict(self._node_coords)

        resolved = []
        for link in lqm_links:
            src = link.get("source", "")
            tgt = link.get("target", "")
            src_coords = node_coords.get(src)
            tgt_coords = node_coords.get(tgt)
            if src_coords and tgt_coords:
                resolved.append({
                    **link,
                    "source_lat": src_coords[0],
                    "source_lon": src_coords[1],
                    "target_lat": tgt_coords[0],
                    "target_lon": tgt_coords[1],
                })
            else:
                # Include unresolved links without coordinates
                resolved.append(link)
        return resolved

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
