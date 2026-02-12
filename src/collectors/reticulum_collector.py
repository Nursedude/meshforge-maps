"""
MeshForge Maps - Reticulum / RMAP Data Collector

Collects node data from Reticulum network sources:
  1. Local RNS path table (rnstatus --json via localhost:37428)
  2. Reticulum Community Hub (RCH) API -- telemetry + node data
  3. MeshForge unified node tracker cache (RNS nodes)
  4. RMAP.world data (rmap.world - community map of RNS nodes)

RMAP.world (https://rmap.world) tracks ~306 nodes including:
  - RNodes (LoRa), NomadNet, RNSD, TCP, I2C, TNC, RetiBBS, LXMF
  - TCP transport interface at rmap.world:4242

Reticulum Community Hub (RCH) by FreeTAKTeam exposes a FastAPI northbound
REST API for telemetry collection and node management over LXMF.
  - GitHub: https://github.com/FreeTAKTeam/Reticulum-Telemetry-Hub
  - PyPI: ReticulumCommunityHub
  - API: /docs (Swagger) on configured host:port

Reticulum nodes use cryptographic destination hashes (128-bit SHA-256 derived)
for identity. Privacy-first design: no source addresses in packets.

See: https://github.com/markqvist/Reticulum/discussions/743
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import (
    MESHFORGE_DATA_DIR,
    UNIFIED_CACHE_PATH,
    BaseCollector,
    deduplicate_features,
    make_feature,
    make_feature_collection,
    validate_coordinates,
)

logger = logging.getLogger(__name__)

# MeshForge RNS cache location
RNS_CACHE_PATH = MESHFORGE_DATA_DIR / "rns_nodes.json"

# Reticulum Community Hub (RCH) API defaults
RCH_DEFAULT_HOST = "localhost"
RCH_DEFAULT_PORT = 8000

# RNS node type mapping for display
RNS_NODE_TYPES = {
    "rnode": "RNode (LoRa)",
    "nomadnet": "NomadNet",
    "rnsd": "RNSD",
    "tcp": "TCP Transport",
    "i2p": "I2P",
    "tnc": "TNC KiSS",
    "retibbs": "RetiBBS",
    "lxmf_group": "LXMF Group",
    "lxmf_peer": "LXMF Peer",
    "multi": "Multi-Interface",
    "yggdrasil": "Yggdrasil",
}


class ReticulumCollector(BaseCollector):
    """Collects Reticulum node data from local RNS, RCH API, and caches."""

    source_name = "reticulum"

    def __init__(
        self,
        rch_host: str = RCH_DEFAULT_HOST,
        rch_port: int = RCH_DEFAULT_PORT,
        rch_api_key: Optional[str] = None,
        cache_ttl_seconds: int = 900,
    ):
        super().__init__(cache_ttl_seconds)
        self._rch_base = f"http://{rch_host}:{rch_port}"
        self._rch_api_key = rch_api_key

    def _fetch(self) -> Dict[str, Any]:
        # Collect from all sources in priority order, then deduplicate
        features = deduplicate_features([
            self._fetch_from_rnstatus(),    # Source 1: local rnstatus
            self._fetch_from_rch(),          # Source 2: RCH API
            self._fetch_from_cache(),        # Source 3: RNS node cache
            self._fetch_from_unified_cache(),  # Source 4: unified cache
        ], allow_no_id=False)

        return make_feature_collection(features, self.source_name)

    def _fetch_from_rnstatus(self) -> List[Dict[str, Any]]:
        """Query local Reticulum instance via rnstatus."""
        features = []
        try:
            result = subprocess.run(
                ["rnstatus", "-d", "--json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.debug("rnstatus not available or failed")
                return features

            data = json.loads(result.stdout)
            interfaces = data.get("interfaces", [])
            for iface in interfaces:
                feature = self._parse_rns_interface(iface)
                if feature:
                    features.append(feature)
            logger.debug("rnstatus returned %d interfaces", len(features))
        except FileNotFoundError:
            logger.debug("rnstatus command not found")
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
            logger.debug("rnstatus failed: %s", e)
        return features

    def _parse_rns_interface(self, iface: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse an RNS interface entry into a GeoJSON feature."""
        coords = validate_coordinates(iface.get("latitude"), iface.get("longitude"))
        if coords is None:
            return None
        lat, lon = coords

        iface_name = iface.get("name", "Unknown")
        iface_type = iface.get("type", "unknown").lower()
        node_type = RNS_NODE_TYPES.get(iface_type, iface_type)

        return make_feature(
            node_id=iface.get("hash", iface_name),
            lat=lat,
            lon=lon,
            network="reticulum",
            name=iface_name,
            node_type=node_type,
            rns_interface_type=iface_type,
            is_online=iface.get("status") == "up",
            description=iface.get("description", ""),
            altitude=iface.get("height"),
        )

    def _fetch_from_rch(self) -> List[Dict[str, Any]]:
        """Fetch node/telemetry data from Reticulum Community Hub (RCH) API.

        RCH (FreeTAKTeam/Reticulum-Telemetry-Hub) exposes a FastAPI northbound
        REST API. Typical endpoints:
          - GET /api/v1/nodes       -> list of known nodes
          - GET /api/v1/telemetry   -> telemetry data with positions
          - GET /api/v1/subscribers -> subscriber registry

        The API auto-documents at /docs (Swagger UI).
        """
        features = []
        # Try telemetry endpoint first (has position data)
        for endpoint in ("/api/v1/telemetry", "/api/v1/nodes"):
            try:
                url = f"{self._rch_base}{endpoint}"
                headers = {"Accept": "application/json"}
                if self._rch_api_key:
                    headers["X-API-Key"] = self._rch_api_key
                req = Request(url, headers=headers)
                with urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())

                nodes = data if isinstance(data, list) else data.get("items", data.get("nodes", []))
                for node in nodes:
                    feature = self._parse_rch_node(node)
                    if feature:
                        features.append(feature)

                if features:
                    logger.debug("RCH API (%s) returned %d nodes", endpoint, len(features))
                    break  # Got data, no need to try next endpoint
            except (URLError, OSError, json.JSONDecodeError, ValueError) as e:
                logger.debug("RCH API %s unavailable: %s", endpoint, e)
        return features

    def _parse_rch_node(self, node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse a node from RCH API response into a GeoJSON feature."""
        # RCH telemetry may include position in various formats
        lat = node.get("latitude") or node.get("lat")
        lon = node.get("longitude") or node.get("lon")

        # Position may be nested under telemetry or location
        if lat is None or lon is None:
            pos = node.get("position", node.get("location", node.get("telemetry", {})))
            if isinstance(pos, dict):
                lat = pos.get("latitude") or pos.get("lat")
                lon = pos.get("longitude") or pos.get("lon")

        coords = validate_coordinates(lat, lon)
        if coords is None:
            return None
        lat, lon = coords

        # Extract identity
        node_id = (
            node.get("destination_hash")
            or node.get("hash")
            or node.get("identity")
            or node.get("id", "")
        )
        name = node.get("display_name") or node.get("name") or str(node_id)[:16]
        node_type = node.get("type", "unknown").lower()
        display_type = RNS_NODE_TYPES.get(node_type, node_type)

        return make_feature(
            node_id=str(node_id),
            lat=lat,
            lon=lon,
            network="reticulum",
            name=name,
            node_type=display_type,
            rns_interface_type=node_type,
            is_online=node.get("online", node.get("is_online")),
            last_seen=node.get("last_seen", node.get("updated_at")),
            description=node.get("description", ""),
            altitude=node.get("altitude") or node.get("height"),
            source="rch",
        )

    def _fetch_from_cache(self) -> List[Dict[str, Any]]:
        """Read MeshForge's RNS node cache."""
        return self._read_cache_file(RNS_CACHE_PATH)

    def _fetch_from_unified_cache(self) -> List[Dict[str, Any]]:
        """Read RNS nodes from MeshForge's unified node cache.

        Reuses _read_cache_file which already filters by reticulum network.
        """
        return self._read_cache_file(UNIFIED_CACHE_PATH)

    def _read_cache_file(self, path: Path) -> List[Dict[str, Any]]:
        """Read a GeoJSON or node-list cache file.

        Filters FeatureCollection entries to only include reticulum network
        nodes, preventing non-RNS features from leaking through shared caches.
        """
        features = []
        if not path.exists():
            return features
        try:
            with open(path, "r") as f:
                data = json.load(f)

            if data.get("type") == "FeatureCollection":
                for feature in data.get("features", []):
                    props = feature.get("properties", {})
                    if props.get("network", "reticulum") == "reticulum":
                        features.append(feature)
            elif isinstance(data, dict):
                for node_id, node_data in data.items():
                    if isinstance(node_data, dict):
                        lat = node_data.get("latitude")
                        lon = node_data.get("longitude")
                        if lat is not None and lon is not None:
                            feature = make_feature(
                                node_id=node_id,
                                lat=lat,
                                lon=lon,
                                network="reticulum",
                                name=node_data.get("name", node_id),
                                node_type=node_data.get("type", "unknown"),
                                is_online=node_data.get("is_online"),
                                last_seen=node_data.get("last_seen"),
                            )
                            if feature is not None:
                                features.append(feature)
            logger.debug("Cache %s returned %d nodes", path.name, len(features))
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("Cache read failed for %s: %s", path.name, e)
        return features
