"""
MeshForge Maps - Config Drift Detection

Detects changes in node configuration over time by comparing successive
observations of node identity and radio parameters. When a node's role,
hardware model, name, or other tracked fields change, a drift event is
recorded and optionally emitted to the event bus.

Drift detection is valuable for mesh network operators:
  - A node suddenly changing role (CLIENT -> ROUTER) may indicate
    unauthorized reconfiguration or firmware update
  - Hardware model changes suggest the node was replaced or re-flashed
  - Name changes may indicate a new operator or config reset

Data sources for drift detection:
  - NODEINFO_APP (portnum 4): role, long_name, short_name, hw_model
  - TELEMETRY_APP (portnum 67): channel_util, air_util_tx patterns
  - MAP_REPORT (portnum 73): LoRa config (region, modem preset, etc.)

Thread-safe: all state behind a lock.
"""

import logging
import threading
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Maximum drift history entries per node
MAX_DRIFT_HISTORY = 50

# Maximum nodes to track (prevents unbounded memory growth)
MAX_TRACKED_NODES = 10000


class DriftSeverity(Enum):
    """Severity of a detected config drift."""
    INFO = "info"           # Cosmetic changes (name)
    WARNING = "warning"     # Operational changes (role, tx_power)
    CRITICAL = "critical"   # Breaking changes (region, modem preset)


# Fields to track and their severity when changed
TRACKED_FIELDS = {
    # From NODEINFO_APP
    "role": DriftSeverity.WARNING,
    "hardware": DriftSeverity.WARNING,
    "name": DriftSeverity.INFO,
    "short_name": DriftSeverity.INFO,
    # From MAP_REPORT or direct config
    "region": DriftSeverity.CRITICAL,
    "modem_preset": DriftSeverity.CRITICAL,
    "hop_limit": DriftSeverity.WARNING,
    "tx_power": DriftSeverity.WARNING,
    "tx_enabled": DriftSeverity.WARNING,
    "channel_name": DriftSeverity.CRITICAL,
    "uplink_enabled": DriftSeverity.INFO,
    "downlink_enabled": DriftSeverity.INFO,
}


class ConfigDriftDetector:
    """Detects and records configuration changes for mesh nodes.

    Maintains a snapshot of each node's last-known configuration and
    compares incoming updates to detect drift.

    Usage:
        detector = ConfigDriftDetector()
        drifts = detector.check_node("!a1b2c3d4", role="ROUTER", hardware="TBEAM")
        # drifts is empty on first observation, populated on subsequent changes
    """

    def __init__(
        self,
        on_drift: Optional[Callable] = None,
        max_history: int = MAX_DRIFT_HISTORY,
        max_nodes: int = MAX_TRACKED_NODES,
    ):
        self._snapshots: Dict[str, Dict[str, Any]] = {}
        self._drift_history: Dict[str, List[Dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._on_drift = on_drift
        self._max_history = max_history
        self._max_nodes = max_nodes
        self._total_drifts = 0

    def check_node(self, node_id: str, **fields: Any) -> List[Dict[str, Any]]:
        """Check a node's current fields against its last-known snapshot.

        Only tracked fields (those in TRACKED_FIELDS) are compared.
        Returns a list of drift records for any changes detected.
        On first observation for a node, records the snapshot and returns [].

        Args:
            node_id: The node identifier (e.g., "!a1b2c3d4")
            **fields: Current field values (role="ROUTER", hardware="TBEAM", etc.)

        Returns:
            List of drift dicts: [{field, old_value, new_value, severity, timestamp}]
        """
        # Filter to tracked fields only, ignoring None values
        current = {
            k: v for k, v in fields.items()
            if k in TRACKED_FIELDS and v is not None
        }
        if not current:
            return []

        now = time.time()
        drifts: List[Dict[str, Any]] = []

        with self._lock:
            previous = self._snapshots.get(node_id)

            if previous is None:
                # First observation — record snapshot, no drift
                if len(self._snapshots) >= self._max_nodes:
                    self._evict_oldest_locked()
                self._snapshots[node_id] = dict(current)
                self._snapshots[node_id]["_first_seen"] = now
                self._snapshots[node_id]["_last_seen"] = now
                return []

            # Compare current values against snapshot
            for field, new_value in current.items():
                old_value = previous.get(field)
                if old_value is not None and str(old_value) != str(new_value):
                    severity = TRACKED_FIELDS.get(field, DriftSeverity.INFO)
                    drift = {
                        "node_id": node_id,
                        "field": field,
                        "old_value": old_value,
                        "new_value": new_value,
                        "severity": severity.value,
                        "timestamp": now,
                    }
                    drifts.append(drift)
                    self._total_drifts += 1

                    # Record in history
                    history = self._drift_history.setdefault(node_id, [])
                    history.append(drift)
                    if len(history) > self._max_history:
                        history.pop(0)

                    logger.info(
                        "Config drift [%s] %s: %s -> %s (%s) on %s",
                        severity.value, field, old_value, new_value,
                        node_id, time.strftime("%H:%M:%S", time.gmtime(now)),
                    )

            # Update snapshot with current values
            previous.update(current)
            previous["_last_seen"] = now

        # Notify callback outside the lock
        if drifts and self._on_drift:
            try:
                self._on_drift(node_id, drifts)
            except Exception as e:
                logger.debug("Drift callback error: %s", e)

        return drifts

    def get_node_snapshot(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Return the current config snapshot for a node."""
        with self._lock:
            snap = self._snapshots.get(node_id)
            return dict(snap) if snap else None

    def get_node_drift_history(self, node_id: str) -> List[Dict[str, Any]]:
        """Return drift history for a specific node."""
        with self._lock:
            return list(self._drift_history.get(node_id, []))

    def get_all_drifts(self, since: Optional[float] = None,
                       severity: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all drift events, optionally filtered by time and severity.

        Args:
            since: Unix timestamp — only return drifts after this time
            severity: Filter by severity level ("info", "warning", "critical")
        """
        with self._lock:
            result = []
            for history in self._drift_history.values():
                for drift in history:
                    if since and drift["timestamp"] < since:
                        continue
                    if severity and drift["severity"] != severity:
                        continue
                    result.append(dict(drift))
            result.sort(key=lambda d: d["timestamp"], reverse=True)
            return result

    def get_summary(self) -> Dict[str, Any]:
        """Return a summary of drift detection state."""
        with self._lock:
            nodes_with_drift = sum(
                1 for h in self._drift_history.values() if h
            )
            recent_drifts = []
            for history in self._drift_history.values():
                recent_drifts.extend(history[-3:])
            recent_drifts.sort(key=lambda d: d["timestamp"], reverse=True)

            return {
                "tracked_nodes": len(self._snapshots),
                "nodes_with_drift": nodes_with_drift,
                "total_drifts": self._total_drifts,
                "recent_drifts": recent_drifts[:10],
            }

    @property
    def tracked_node_count(self) -> int:
        with self._lock:
            return len(self._snapshots)

    @property
    def total_drifts(self) -> int:
        return self._total_drifts

    def remove_node(self, node_id: str) -> None:
        """Remove all tracking data for a node (e.g., after eviction)."""
        with self._lock:
            self._snapshots.pop(node_id, None)
            self._drift_history.pop(node_id, None)

    def _evict_oldest_locked(self) -> None:
        """Evict the node with the oldest last_seen. Must hold lock."""
        if not self._snapshots:
            return
        oldest_id = min(
            self._snapshots,
            key=lambda nid: self._snapshots[nid].get("_last_seen", 0),
        )
        del self._snapshots[oldest_id]
        self._drift_history.pop(oldest_id, None)
