"""
MeshForge Maps - Mesh Client Collector

Ingests a GeoJSON snapshot file written periodically by
`meshing_around_meshforge` (the mesh-client / wx-bot), so mesh-client users see
*their own* nodes on the map alongside the meshtastic / MQTT / AREDN / meshcore
feeds. The mesh-client is the writer; this collector is the read side (issue #78).

Writer contract (meshing_around_meshforge `[maps_export]`):
  - Atomic write: temp file + rename(2), so a reader never sees a half file.
  - Top-level ``updated`` ISO timestamp so an unchanged snapshot is skipped.
  - ``FeatureCollection`` of Point features with properties:
    {node_id, name, short_name, long_name, hardware_model, is_online,
     last_heard, altitude, battery_level?, channel_utilization?, snr?,
     quality_percent?}

Design notes:
  - Nodes are tagged ``network="meshtastic"`` (they *are* meshtastic nodes — so
    they get the correct map colour/legend and dedup automatically by node id
    against MeshtasticCollector) and ``source="mesh_client"`` so the feed stays
    identifiable.
  - ``is_online`` is recomputed from ``last_heard`` via the dedicated
    ``mesh_client`` online threshold rather than trusting the writer's snapshot
    value (which can be stale by the time we read it).
  - Every read error (missing file, oversize, bad JSON, wrong type) degrades to
    an empty FeatureCollection — never an exception — mirroring AREDN's
    file-cache reader. Absence of the file is normal (writer opt-in / not yet
    written), not an error.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from .base import (
    NODE_ID_RE,
    BaseCollector,
    is_node_online,
    make_feature,
    make_feature_collection,
)

logger = logging.getLogger(__name__)

# Mirror the 10 MB cap the HTTP collectors enforce via bounded_read(): a
# runaway or corrupted writer should not be able to make us allocate unbounded
# memory parsing one file.
DEFAULT_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB


class MeshClientCollector(BaseCollector):
    """Reads a GeoJSON node snapshot written by meshing_around_meshforge."""

    source_name = "mesh_client"

    def __init__(
        self,
        path: str,
        cache_ttl_seconds: int = 900,
        max_retries: int = 0,
        max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ):
        # No retries by default: a missing/bad local file won't fix itself on an
        # immediate retry, and _fetch() never raises (it degrades to empty), so
        # the BaseCollector retry loop is a no-op here anyway.
        super().__init__(cache_ttl_seconds=cache_ttl_seconds, max_retries=max_retries)
        self._path = path
        self._max_bytes = max_bytes
        # Skip re-normalizing an unchanged snapshot (keyed on the writer's
        # top-level `updated` stamp).
        self._last_updated: Optional[Any] = None
        self._last_fc: Optional[Dict[str, Any]] = None

    def _empty(self) -> Dict[str, Any]:
        return make_feature_collection([], self.source_name)

    def _fetch(self) -> Dict[str, Any]:
        path = self._path
        if not path or not os.path.exists(path):
            # Normal when the writer is opt-in / not yet running. Not an error.
            logger.debug("mesh_client: snapshot file not present at %s", path)
            return self._empty()

        try:
            size = os.path.getsize(path)
        except OSError as e:
            logger.debug("mesh_client: cannot stat %s: %s", path, e)
            return self._empty()

        if size > self._max_bytes:
            logger.warning(
                "mesh_client: snapshot %s is %d bytes (> %d cap) — refusing to parse",
                path, size, self._max_bytes,
            )
            return self._empty()

        try:
            with open(path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, ValueError, UnicodeDecodeError) as e:
            logger.warning("mesh_client: failed to read/parse %s: %s", path, e)
            return self._empty()

        if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
            logger.warning("mesh_client: %s is not a FeatureCollection — ignoring", path)
            return self._empty()

        # Reuse the prior normalization when the writer's snapshot is unchanged.
        updated = data.get("updated")
        if updated is not None and updated == self._last_updated and self._last_fc is not None:
            logger.debug("mesh_client: snapshot unchanged (updated=%s) — reusing parse", updated)
            return self._last_fc

        raw_features = data.get("features")
        if not isinstance(raw_features, list):
            raw_features = []

        features = []
        for feat in raw_features:
            norm = self._normalize_feature(feat)
            if norm is not None:
                features.append(norm)

        fc = make_feature_collection(features, self.source_name)
        self._last_updated = updated
        self._last_fc = fc
        logger.info(
            "mesh_client: ingested %d/%d nodes from %s",
            len(features), len(raw_features), path,
        )
        return fc

    def _normalize_feature(self, feat: Any) -> Optional[Dict[str, Any]]:
        """Rebuild one writer feature into a validated, canonical map feature.

        Returns None (skip) for malformed entries: non-dict, missing/short
        coordinates, missing/invalid node id, or coordinates that fail
        validation (NaN/Inf/out-of-range/Null-Island, handled by make_feature).
        """
        if not isinstance(feat, dict):
            return None
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates")
        if not isinstance(coords, (list, tuple)) or len(coords) < 2:
            return None
        props = feat.get("properties")
        if not isinstance(props, dict):
            return None

        node_id = props.get("node_id") or props.get("id")
        if not isinstance(node_id, str) or not NODE_ID_RE.match(node_id):
            return None

        # The writer emits last_heard as an ISO-8601 string, but is_node_online()
        # and the rest of the pipeline use numeric epochs — coerce it.
        last_heard = self._to_epoch(props.get("last_heard"))
        name = (
            props.get("long_name")
            or props.get("name")
            or props.get("short_name")
            or node_id
        )

        # GeoJSON coordinate order is [lon, lat].
        return make_feature(
            node_id=node_id,
            lat=coords[1],
            lon=coords[0],
            network="meshtastic",
            name=name,
            node_type="meshtastic_node",
            source="mesh_client",
            hardware=props.get("hardware_model"),
            last_seen=last_heard,
            is_online=is_node_online(last_heard, "mesh_client"),
            altitude=props.get("altitude"),
            battery=props.get("battery_level"),
            snr=props.get("snr"),
            short_name=props.get("short_name"),
            channel_utilization=props.get("channel_utilization"),
            quality_percent=props.get("quality_percent"),
        )

    @staticmethod
    def _to_epoch(value: Any) -> Optional[float]:
        """Coerce the writer's last_heard to a Unix epoch float.

        meshing_around's get_geojson() emits last_heard as an ISO-8601 string
        (``node.last_heard.isoformat()``), but ``is_node_online()`` and the map
        pipeline use numeric epochs. Accepts ISO-8601, a numeric epoch, or a
        numeric string; returns None for missing/empty/unparseable values (so a
        node with no last_heard reads as "unknown", never falsely online).
        """
        if value is None:
            return None
        if isinstance(value, bool):
            # bool is an int subclass — a stray True/False is not a timestamp.
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            try:
                return float(s)  # numeric epoch as a string
            except ValueError:
                pass
            try:
                # fromisoformat handles offsets + microseconds on 3.9; normalize
                # a trailing 'Z' which it only accepts natively from 3.11.
                return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                return None
        return None
