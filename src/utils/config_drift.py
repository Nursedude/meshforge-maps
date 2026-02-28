"""
MeshForge Maps - Config Drift Detection

Detects changes in node configuration over time by comparing successive
observations. When a tracked field changes, a drift event is recorded
and optionally dispatched to a callback.

Data sources:
  - NODEINFO_APP (portnum 4): role, long_name, short_name, hw_model
  - MAP_REPORT (portnum 73): LoRa config (region, modem preset, etc.)

Thread-safe: all state behind a lock.
"""

import logging
import threading
import time
from collections import deque
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default limits
MAX_DRIFT_HISTORY = 50
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


def _normalize_value(value: Any) -> str:
    """Normalize a config value so int(1) == float(1.0) in comparisons."""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


class ConfigDriftDetector:
    """Detects and records configuration changes for mesh nodes.

    Usage:
        detector = ConfigDriftDetector()
        drifts = detector.check_node("!a1b2c3d4", role="ROUTER", hardware="TBEAM")
        # Empty on first observation, populated on subsequent changes.
    """

    def __init__(
        self,
        on_drift: Optional[Callable] = None,
        max_history: int = MAX_DRIFT_HISTORY,
        max_nodes: int = MAX_TRACKED_NODES,
    ):
        self._snapshots: Dict[str, Dict[str, Any]] = {}
        self._drift_history: Dict[str, deque] = {}
        self._lock = threading.Lock()
        self._on_drift = on_drift
        self._max_history = max_history
        self._max_nodes = max_nodes
        self._total_drifts = 0

    def check_node(self, node_id: str, **fields: Any) -> List[Dict[str, Any]]:
        """Compare a node's current fields against its last-known snapshot.

        Returns a list of drift records for detected changes.
        On first observation, records the snapshot and returns [].
        """
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
                if len(self._snapshots) >= self._max_nodes:
                    self._evict_oldest_locked()
                self._snapshots[node_id] = {
                    **current, "_first_seen": now, "_last_seen": now,
                }
                return []

            for field, new_value in current.items():
                old_value = previous.get(field)
                if old_value is None:
                    continue
                if _normalize_value(old_value) == _normalize_value(new_value):
                    continue

                severity = TRACKED_FIELDS[field]
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

                history = self._drift_history.get(node_id)
                if history is None:
                    history = deque(maxlen=self._max_history)
                    self._drift_history[node_id] = history
                history.append(drift)

                logger.info(
                    "Config drift [%s] %s: %s -> %s (%s)",
                    severity.value, field, old_value, new_value, node_id,
                )

            previous.update(current)
            previous["_last_seen"] = now

        # Notify callback outside the lock
        if drifts and self._on_drift:
            try:
                self._on_drift(node_id, drifts)
            except Exception as e:
                logger.warning("Drift callback error: %s", e)

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
        """Return all drift events, optionally filtered by time and severity."""
        with self._lock:
            result = []
            for history in self._drift_history.values():
                for drift in history:
                    if since is not None and drift["timestamp"] < since:
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
                recent_drifts.extend(list(history)[-3:])
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
        with self._lock:
            return self._total_drifts

    def remove_node(self, node_id: str) -> None:
        """Remove all tracking data for a node."""
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
