"""
MeshForge Maps - Reticulum / RMAP Data Collector

Collects node data from Reticulum network sources:
  1. Local RNS path table (rnstatus --json via localhost:37428)
  2. MeshForge unified node tracker cache (RNS nodes)
  3. RMAP.world data (rmap.world - community map of RNS nodes)

RMAP.world (https://rmap.world) tracks ~306 nodes including:
  - RNodes (LoRa), NomadNet, RNSD, TCP, I2C, TNC, RetiBBS, LXMF
  - TCP transport interface at rmap.world:4242
  - No public REST API currently; future GitHub repo planned

Reticulum nodes use cryptographic destination hashes (128-bit SHA-256 derived)
for identity. Privacy-first design: no source addresses in packets.

See: https://github.com/markqvist/Reticulum/discussions/743
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import BaseCollector, make_feature, make_feature_collection

logger = logging.getLogger(__name__)

# MeshForge RNS cache locations
RNS_CACHE_PATH = Path.home() / ".local" / "share" / "meshforge" / "rns_nodes.json"
NODE_CACHE_PATH = Path.home() / ".local" / "share" / "meshforge" / "node_cache.json"

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
    """Collects Reticulum node data from local RNS and cached sources."""

    source_name = "reticulum"

    def _fetch(self) -> Dict[str, Any]:
        features: List[Dict[str, Any]] = []
        seen_ids: set = set()

        # Source 1: rnstatus JSON output (local Reticulum instance)
        rns_nodes = self._fetch_from_rnstatus()
        for f in rns_nodes:
            fid = f["properties"].get("id")
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                features.append(f)

        # Source 2: MeshForge RNS node cache
        cache_nodes = self._fetch_from_cache()
        for f in cache_nodes:
            fid = f["properties"].get("id")
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                features.append(f)

        # Source 3: MeshForge unified node cache (RNS entries)
        unified_nodes = self._fetch_from_unified_cache()
        for f in unified_nodes:
            fid = f["properties"].get("id")
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                features.append(f)

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
        lat = iface.get("latitude")
        lon = iface.get("longitude")
        if lat is None or lon is None:
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None

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

    def _fetch_from_cache(self) -> List[Dict[str, Any]]:
        """Read MeshForge's RNS node cache."""
        return self._read_cache_file(RNS_CACHE_PATH)

    def _fetch_from_unified_cache(self) -> List[Dict[str, Any]]:
        """Read RNS nodes from MeshForge's unified node cache."""
        features = []
        if not NODE_CACHE_PATH.exists():
            return features
        try:
            with open(NODE_CACHE_PATH, "r") as f:
                data = json.load(f)

            if data.get("type") == "FeatureCollection":
                for feature in data.get("features", []):
                    props = feature.get("properties", {})
                    if props.get("network") == "reticulum":
                        features.append(feature)
            logger.debug("Unified cache returned %d RNS nodes", len(features))
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("Unified cache read failed: %s", e)
        return features

    def _read_cache_file(self, path: Path) -> List[Dict[str, Any]]:
        """Read a GeoJSON or node-list cache file."""
        features = []
        if not path.exists():
            return features
        try:
            with open(path, "r") as f:
                data = json.load(f)

            if data.get("type") == "FeatureCollection":
                features = data.get("features", [])
            elif isinstance(data, dict):
                for node_id, node_data in data.items():
                    if isinstance(node_data, dict):
                        lat = node_data.get("latitude")
                        lon = node_data.get("longitude")
                        if lat is not None and lon is not None:
                            features.append(
                                make_feature(
                                    node_id=node_id,
                                    lat=lat,
                                    lon=lon,
                                    network="reticulum",
                                    name=node_data.get("name", node_id),
                                    node_type=node_data.get("type", "unknown"),
                                    is_online=node_data.get("is_online"),
                                    last_seen=node_data.get("last_seen"),
                                )
                            )
            logger.debug("Cache %s returned %d nodes", path.name, len(features))
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("Cache read failed for %s: %s", path.name, e)
        return features
