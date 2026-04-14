"""
MeshForge Maps - MeshCore Data Collector

Collects node data from the MeshCore mesh network via the public map API.
MeshCore is an intelligent-routing LoRa mesh protocol (separate from Meshtastic).

Data source: https://map.meshcore.dev/api/v1/nodes
  - ~30,000 nodes with GPS positions
  - Node types: client (1), repeater (2), room server (3)
  - RF params: frequency, spreading factor, coding rate, bandwidth
  - No authentication required

See: https://meshcore.co.uk/
"""

import json
import logging
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import (
    BaseCollector,
    is_node_online,
    make_feature,
    make_feature_collection,
    point_in_bboxes,
    validate_coordinates,
)

logger = logging.getLogger(__name__)

# MeshCore public map API
MESHCORE_MAP_URL = "https://map.meshcore.dev/api/v1/nodes"

# Node type mapping (from MeshCore protocol)
MESHCORE_NODE_TYPES = {
    1: "client",
    2: "repeater",
    3: "room_server",
}


class MeshCoreCollector(BaseCollector):
    """Collects MeshCore node data from the public map API."""

    source_name = "meshcore"

    def __init__(
        self,
        enable_map: bool = True,
        cache_ttl_seconds: int = 1800,
        max_retries: int = 0,
        region_bboxes: Optional[List[List[float]]] = None,
    ):
        super().__init__(cache_ttl_seconds, max_retries=max_retries)
        self._enable_map = enable_map
        self._region_bboxes = region_bboxes

    def _fetch(self) -> Dict[str, Any]:
        features: List[Dict[str, Any]] = []
        if self._enable_map:
            features = self._fetch_from_meshcore_map()
        return make_feature_collection(features, self.source_name)

    def _fetch_from_meshcore_map(self) -> List[Dict[str, Any]]:
        """Fetch MeshCore node data from the public map API."""
        features = []
        try:
            req = Request(
                MESHCORE_MAP_URL,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "MeshForge/1.0",
                },
            )
            with urlopen(req, timeout=30) as resp:
                # API may redirect (307), urlopen follows by default for GET
                data = json.loads(resp.read().decode("utf-8", errors="replace"))

            if not isinstance(data, list):
                logger.debug("MeshCore map: unexpected response format")
                return features

            skipped_oob = 0
            for node in data:
                if self._region_bboxes and not point_in_bboxes(
                    node.get("adv_lat"), node.get("adv_lon"), self._region_bboxes
                ):
                    skipped_oob += 1
                    continue
                feature = self._parse_meshcore_node(node)
                if feature:
                    features.append(feature)
            if self._region_bboxes and skipped_oob:
                logger.debug("MeshCore map: skipped %d nodes outside region bbox", skipped_oob)

            if features:
                logger.debug("MeshCore map returned %d nodes", len(features))
        except (URLError, OSError, json.JSONDecodeError, ValueError) as e:
            logger.debug("MeshCore map unavailable: %s", e)
        return features

    def _parse_meshcore_node(
        self, node: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Parse a node from the MeshCore map API into a GeoJSON feature."""
        coords = validate_coordinates(
            node.get("adv_lat"), node.get("adv_lon")
        )
        if coords is None:
            return None
        lat, lon = coords

        public_key = node.get("public_key", "")
        if not public_key:
            return None

        name = node.get("adv_name") or public_key[:16]
        node_type_id = node.get("type", 0)
        node_type = MESHCORE_NODE_TYPES.get(node_type_id, "unknown")

        params = node.get("params") or {}

        return make_feature(
            node_id=public_key,
            lat=lat,
            lon=lon,
            network="meshcore",
            name=name,
            node_type=node_type,
            last_seen=node.get("last_advert"),
            is_online=is_node_online(node.get("last_advert"), "meshcore"),
            frequency=params.get("freq"),
            spreading_factor=params.get("sf"),
            coding_rate=params.get("cr"),
            bandwidth=params.get("bw"),
            source="meshcore_map",
        )
