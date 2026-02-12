"""
MeshForge Maps - Node Intermittent State Machine

Tracks node connectivity patterns and classifies each node into one of
four states based on heartbeat regularity:

  NEW         -> First observation, not enough data to classify
  STABLE      -> Node is consistently reporting (regular heartbeats)
  INTERMITTENT -> Node reports sporadically (gaps > 2x expected interval)
  OFFLINE     -> Node has not been seen for longer than offline_threshold

State transitions are driven by calling `record_heartbeat(node_id)` each
time a node is observed (position, telemetry, nodeinfo). The state machine
maintains a sliding window of recent heartbeat timestamps to compute
regularity metrics.

This is useful for:
  - Identifying nodes with connectivity issues
  - Filtering map display by reliability
  - Alerting operators to degraded nodes
  - Tracking mesh health trends over time

Thread-safe: all state behind a lock.
"""

import logging
import threading
import time
from collections import deque
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Maximum heartbeats to retain per node (sliding window)
MAX_HEARTBEAT_WINDOW = 20

# Maximum nodes to track
MAX_TRACKED_NODES = 10000

# Default thresholds
DEFAULT_EXPECTED_INTERVAL = 300    # 5 minutes — typical Meshtastic position broadcast
DEFAULT_OFFLINE_THRESHOLD = 3600   # 1 hour — no heartbeat = offline
DEFAULT_INTERMITTENT_RATIO = 0.5   # <50% of expected heartbeats = intermittent


class NodeState(Enum):
    """Connectivity state for a mesh node."""
    NEW = "new"
    STABLE = "stable"
    INTERMITTENT = "intermittent"
    OFFLINE = "offline"


class NodeStateEntry:
    """Internal state tracking for a single node."""

    __slots__ = (
        "node_id", "state", "heartbeats", "first_seen",
        "last_seen", "transition_count", "last_transition",
    )

    def __init__(self, node_id: str, timestamp: float, max_window: int = MAX_HEARTBEAT_WINDOW):
        self.node_id = node_id
        self.state = NodeState.NEW
        self.heartbeats: deque = deque([timestamp], maxlen=max_window)
        self.first_seen = timestamp
        self.last_seen = timestamp
        self.transition_count = 0
        self.last_transition = timestamp

    def add_heartbeat(self, timestamp: float, max_window: int) -> None:
        self.heartbeats.append(timestamp)
        self.last_seen = timestamp

    def average_interval(self) -> Optional[float]:
        """Compute average interval between heartbeats."""
        if len(self.heartbeats) < 2:
            return None
        intervals = [
            self.heartbeats[i] - self.heartbeats[i - 1]
            for i in range(1, len(self.heartbeats))
        ]
        return sum(intervals) / len(intervals)

    def gap_ratio(self, expected_interval: float) -> float:
        """Fraction of intervals that exceed 2x the expected interval.

        Returns 0.0 if all intervals are within tolerance, 1.0 if all are gaps.
        """
        if len(self.heartbeats) < 2:
            return 0.0
        gap_threshold = expected_interval * 2
        intervals = [
            self.heartbeats[i] - self.heartbeats[i - 1]
            for i in range(1, len(self.heartbeats))
        ]
        gaps = sum(1 for iv in intervals if iv > gap_threshold)
        return gaps / len(intervals)

    def to_dict(self) -> Dict[str, Any]:
        avg = self.average_interval()
        return {
            "node_id": self.node_id,
            "state": self.state.value,
            "heartbeat_count": len(self.heartbeats),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "average_interval": round(avg, 1) if avg else None,
            "transition_count": self.transition_count,
        }


class NodeStateTracker:
    """Tracks node connectivity patterns and classifies state.

    Usage:
        tracker = NodeStateTracker()
        old_state, new_state = tracker.record_heartbeat("!a1b2c3d4")
        if old_state != new_state:
            print(f"Node transitioned: {old_state} -> {new_state}")
    """

    def __init__(
        self,
        expected_interval: float = DEFAULT_EXPECTED_INTERVAL,
        offline_threshold: float = DEFAULT_OFFLINE_THRESHOLD,
        intermittent_ratio: float = DEFAULT_INTERMITTENT_RATIO,
        heartbeat_window: int = MAX_HEARTBEAT_WINDOW,
        on_transition: Optional[Callable] = None,
        max_nodes: int = MAX_TRACKED_NODES,
    ):
        self._expected_interval = expected_interval
        self._offline_threshold = offline_threshold
        self._intermittent_ratio = intermittent_ratio
        self._heartbeat_window = heartbeat_window
        self._on_transition = on_transition
        self._max_nodes = max_nodes
        self._nodes: Dict[str, NodeStateEntry] = {}
        self._lock = threading.Lock()
        self._total_transitions = 0

    def record_heartbeat(
        self, node_id: str, timestamp: Optional[float] = None
    ) -> tuple:
        """Record a heartbeat for a node and recompute its state.

        Args:
            node_id: The node identifier
            timestamp: Observation time (defaults to now)

        Returns:
            (old_state, new_state) tuple of NodeState values
        """
        if timestamp is None:
            timestamp = time.time()

        transition = None

        with self._lock:
            entry = self._nodes.get(node_id)
            if entry is None:
                if len(self._nodes) >= self._max_nodes:
                    self._evict_oldest_locked()
                entry = NodeStateEntry(node_id, timestamp, self._heartbeat_window)
                self._nodes[node_id] = entry
                return (NodeState.NEW, NodeState.NEW)

            old_state = entry.state
            entry.add_heartbeat(timestamp, self._heartbeat_window)
            new_state = self._classify(entry)

            if new_state != old_state:
                entry.state = new_state
                entry.transition_count += 1
                entry.last_transition = timestamp
                self._total_transitions += 1
                transition = (node_id, old_state, new_state)

        # Fire callback outside lock
        if transition and self._on_transition:
            try:
                self._on_transition(*transition)
            except Exception as e:
                logger.debug("State transition callback error: %s", e)

        return (old_state, new_state if transition else old_state)

    def check_offline(self, now: Optional[float] = None) -> List[str]:
        """Check all nodes for offline transitions.

        Nodes not seen within offline_threshold are transitioned to OFFLINE.
        Returns list of node IDs that transitioned.

        Call this periodically (e.g., every minute) from a cleanup loop.
        """
        if now is None:
            now = time.time()

        transitioned = []
        transitions = []

        with self._lock:
            for node_id, entry in self._nodes.items():
                if entry.state == NodeState.OFFLINE:
                    continue
                age = now - entry.last_seen
                if age > self._offline_threshold:
                    old_state = entry.state
                    entry.state = NodeState.OFFLINE
                    entry.transition_count += 1
                    entry.last_transition = now
                    self._total_transitions += 1
                    transitioned.append(node_id)
                    transitions.append((node_id, old_state, NodeState.OFFLINE))

        # Fire callbacks outside lock
        if self._on_transition:
            for t in transitions:
                try:
                    self._on_transition(*t)
                except Exception as e:
                    logger.debug("State transition callback error: %s", e)

        return transitioned

    def get_node_state(self, node_id: str) -> Optional[NodeState]:
        """Get the current state of a specific node."""
        with self._lock:
            entry = self._nodes.get(node_id)
            return entry.state if entry else None

    def get_node_info(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get full state info for a specific node."""
        with self._lock:
            entry = self._nodes.get(node_id)
            return entry.to_dict() if entry else None

    def get_all_states(self) -> Dict[str, str]:
        """Return {node_id: state_value} for all tracked nodes."""
        with self._lock:
            return {
                nid: entry.state.value
                for nid, entry in self._nodes.items()
            }

    def get_summary(self) -> Dict[str, Any]:
        """Return summary of node states."""
        with self._lock:
            counts = {s.value: 0 for s in NodeState}
            for entry in self._nodes.values():
                counts[entry.state.value] += 1
            return {
                "tracked_nodes": len(self._nodes),
                "states": counts,
                "total_transitions": self._total_transitions,
            }

    def get_nodes_by_state(self, state: NodeState) -> List[Dict[str, Any]]:
        """Return info dicts for all nodes in a given state."""
        with self._lock:
            return [
                entry.to_dict()
                for entry in self._nodes.values()
                if entry.state == state
            ]

    @property
    def tracked_node_count(self) -> int:
        with self._lock:
            return len(self._nodes)

    @property
    def total_transitions(self) -> int:
        with self._lock:
            return self._total_transitions

    def remove_node(self, node_id: str) -> None:
        """Remove all tracking data for a node (e.g., after eviction)."""
        with self._lock:
            self._nodes.pop(node_id, None)

    def _classify(self, entry: NodeStateEntry) -> NodeState:
        """Classify a node's current state based on heartbeat pattern."""
        # Need at least 3 heartbeats to classify as stable/intermittent
        if len(entry.heartbeats) < 3:
            return NodeState.NEW

        gap_ratio = entry.gap_ratio(self._expected_interval)
        if gap_ratio >= self._intermittent_ratio:
            return NodeState.INTERMITTENT
        return NodeState.STABLE

    def _evict_oldest_locked(self) -> None:
        """Evict the node with the oldest last_seen. Must hold lock."""
        if not self._nodes:
            return
        oldest_id = min(
            self._nodes,
            key=lambda nid: self._nodes[nid].last_seen,
        )
        del self._nodes[oldest_id]
