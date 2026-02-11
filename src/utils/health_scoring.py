"""
MeshForge Maps - Per-Node Health Scoring

Computes a composite health score (0-100) for each mesh node based on
available telemetry data. Scores are broken into five weighted components:

  Battery       (0-25):  Battery level and voltage
  Signal        (0-25):  SNR quality and hop distance
  Freshness     (0-20):  Time since last observation
  Reliability   (0-15):  Connectivity state (stable/intermittent/offline)
  Congestion    (0-15):  Channel utilization and TX air time

Not all nodes report all metrics. The scorer normalizes weights so that
only available components are considered: a node reporting only battery
and freshness is scored out of 45 (25+20) and scaled to 0-100.

Thread-safe: all state behind a lock.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Score component weights (max points)
WEIGHT_BATTERY = 25
WEIGHT_SIGNAL = 25
WEIGHT_FRESHNESS = 20
WEIGHT_RELIABILITY = 15
WEIGHT_CONGESTION = 15

# Battery thresholds
BATTERY_FULL = 80       # >= 80% = full score
BATTERY_LOW = 20        # <= 20% = zero score
VOLTAGE_MIN = 3.0       # Below 3.0V = critical (Li-ion)
VOLTAGE_HEALTHY = 3.7   # Above 3.7V = healthy

# SNR thresholds (dB) — aligned with existing 5-tier system
SNR_EXCELLENT = 8.0
SNR_GOOD = 5.0
SNR_MARGINAL = 0.0
SNR_POOR = -10.0

# Hop distance scoring
MAX_HOPS_SCORED = 7     # Beyond 7 hops = minimum score

# Freshness thresholds (seconds)
FRESH_THRESHOLD = 300       # 5 min — full freshness score
STALE_THRESHOLD = 3600      # 1 hour — zero freshness score

# Channel utilization thresholds (%)
CHANNEL_UTIL_LOW = 25       # Below 25% = no congestion
CHANNEL_UTIL_HIGH = 75      # Above 75% = severe congestion

# Maximum nodes to track scores for
MAX_SCORED_NODES = 10000

# Score status labels
SCORE_LABELS = {
    (80, 101): "excellent",
    (60, 80): "good",
    (40, 60): "fair",
    (20, 40): "poor",
    (0, 20): "critical",
}


def _score_label(score: int) -> str:
    """Map a 0-100 score to a status label."""
    for (lo, hi), label in SCORE_LABELS.items():
        if lo <= score < hi:
            return label
    return "unknown"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _linear_score(value: float, bad: float, good: float, max_points: float) -> float:
    """Linear interpolation between bad (0 points) and good (max points)."""
    if good == bad:
        return max_points if value >= good else 0.0
    ratio = (value - bad) / (good - bad)
    return _clamp(ratio, 0.0, 1.0) * max_points


class NodeHealthScore:
    """Computed health score for a single node."""

    __slots__ = (
        "node_id", "score", "status", "components",
        "available_weight", "timestamp",
    )

    def __init__(
        self,
        node_id: str,
        score: int,
        status: str,
        components: Dict[str, Dict[str, Any]],
        available_weight: int,
        timestamp: float,
    ):
        self.node_id = node_id
        self.score = score
        self.status = status
        self.components = components
        self.available_weight = available_weight
        self.timestamp = timestamp

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "score": self.score,
            "status": self.status,
            "components": self.components,
            "available_weight": self.available_weight,
            "timestamp": self.timestamp,
        }


class NodeHealthScorer:
    """Computes and caches per-node health scores.

    Usage:
        scorer = NodeHealthScorer()
        score = scorer.score_node("!a1b2c3d4", node_properties, node_state)
        summary = scorer.get_summary()
    """

    def __init__(
        self,
        max_nodes: int = MAX_SCORED_NODES,
        freshness_fresh: float = FRESH_THRESHOLD,
        freshness_stale: float = STALE_THRESHOLD,
    ):
        self._max_nodes = max_nodes
        self._freshness_fresh = freshness_fresh
        self._freshness_stale = freshness_stale
        self._scores: Dict[str, NodeHealthScore] = {}
        self._lock = threading.Lock()

    def score_node(
        self,
        node_id: str,
        props: Dict[str, Any],
        connectivity_state: Optional[str] = None,
        now: Optional[float] = None,
    ) -> NodeHealthScore:
        """Compute health score for a node from its GeoJSON properties.

        Args:
            node_id: Node identifier
            props: GeoJSON feature properties dict
            connectivity_state: NodeState value from NodeStateTracker
                                ("new", "stable", "intermittent", "offline")
            now: Current timestamp (defaults to time.time())

        Returns:
            NodeHealthScore with composite score 0-100
        """
        if now is None:
            now = time.time()

        components: Dict[str, Dict[str, Any]] = {}
        earned = 0.0
        available = 0

        # --- Battery component (0-25) ---
        battery_result = self._score_battery(props)
        if battery_result is not None:
            points, detail = battery_result
            components["battery"] = {"score": round(points, 1), "max": WEIGHT_BATTERY, **detail}
            earned += points
            available += WEIGHT_BATTERY

        # --- Signal component (0-25) ---
        signal_result = self._score_signal(props)
        if signal_result is not None:
            points, detail = signal_result
            components["signal"] = {"score": round(points, 1), "max": WEIGHT_SIGNAL, **detail}
            earned += points
            available += WEIGHT_SIGNAL

        # --- Freshness component (0-20) ---
        freshness_result = self._score_freshness(props, now)
        if freshness_result is not None:
            points, detail = freshness_result
            components["freshness"] = {"score": round(points, 1), "max": WEIGHT_FRESHNESS, **detail}
            earned += points
            available += WEIGHT_FRESHNESS

        # --- Reliability component (0-15) ---
        reliability_result = self._score_reliability(connectivity_state)
        if reliability_result is not None:
            points, detail = reliability_result
            components["reliability"] = {"score": round(points, 1), "max": WEIGHT_RELIABILITY, **detail}
            earned += points
            available += WEIGHT_RELIABILITY

        # --- Congestion component (0-15) ---
        congestion_result = self._score_congestion(props)
        if congestion_result is not None:
            points, detail = congestion_result
            components["congestion"] = {"score": round(points, 1), "max": WEIGHT_CONGESTION, **detail}
            earned += points
            available += WEIGHT_CONGESTION

        # Normalize to 0-100 based on available weight
        if available > 0:
            normalized = int(round((earned / available) * 100))
        else:
            normalized = 0

        normalized = max(0, min(100, normalized))
        status = _score_label(normalized)

        result = NodeHealthScore(
            node_id=node_id,
            score=normalized,
            status=status,
            components=components,
            available_weight=available,
            timestamp=now,
        )

        with self._lock:
            if len(self._scores) >= self._max_nodes and node_id not in self._scores:
                self._evict_oldest_locked()
            self._scores[node_id] = result

        return result

    def get_node_score(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get cached score for a node."""
        with self._lock:
            entry = self._scores.get(node_id)
            return entry.to_dict() if entry else None

    def get_all_scores(self) -> Dict[str, int]:
        """Return {node_id: score} for all scored nodes."""
        with self._lock:
            return {nid: s.score for nid, s in self._scores.items()}

    def get_summary(self) -> Dict[str, Any]:
        """Return summary statistics of all scored nodes."""
        with self._lock:
            if not self._scores:
                return {
                    "scored_nodes": 0,
                    "average_score": 0,
                    "status_counts": {},
                    "component_averages": {},
                }

            scores = [s.score for s in self._scores.values()]
            avg = sum(scores) / len(scores)

            status_counts: Dict[str, int] = {}
            for s in self._scores.values():
                status_counts[s.status] = status_counts.get(s.status, 0) + 1

            # Average per component
            comp_totals: Dict[str, List[float]] = {}
            for s in self._scores.values():
                for comp_name, comp_data in s.components.items():
                    if comp_name not in comp_totals:
                        comp_totals[comp_name] = []
                    comp_totals[comp_name].append(comp_data["score"])

            comp_avgs = {
                name: round(sum(vals) / len(vals), 1)
                for name, vals in comp_totals.items()
            }

            return {
                "scored_nodes": len(self._scores),
                "average_score": round(avg, 1),
                "min_score": min(scores),
                "max_score": max(scores),
                "status_counts": status_counts,
                "component_averages": comp_avgs,
            }

    def remove_node(self, node_id: str) -> None:
        """Remove cached score for a node (e.g., after eviction)."""
        with self._lock:
            self._scores.pop(node_id, None)

    @property
    def scored_node_count(self) -> int:
        with self._lock:
            return len(self._scores)

    # --- Component scorers ---

    def _score_battery(self, props: Dict[str, Any]) -> Optional[Tuple[float, Dict[str, Any]]]:
        """Score battery health from battery level and/or voltage."""
        battery = props.get("battery")
        voltage = props.get("voltage")

        if battery is None and voltage is None:
            return None

        points = 0.0
        detail: Dict[str, Any] = {}

        if battery is not None:
            try:
                battery = int(battery)
                battery = _clamp(battery, 0, 100)
            except (ValueError, TypeError):
                battery = None

        if voltage is not None:
            try:
                voltage = float(voltage)
            except (ValueError, TypeError):
                voltage = None

        # After conversion attempts, check if we still have any valid data
        if battery is None and voltage is None:
            return None

        if battery is not None and voltage is not None:
            # Both available: 60% battery level, 40% voltage
            batt_score = _linear_score(battery, BATTERY_LOW, BATTERY_FULL, WEIGHT_BATTERY * 0.6)
            volt_score = _linear_score(voltage, VOLTAGE_MIN, VOLTAGE_HEALTHY, WEIGHT_BATTERY * 0.4)
            points = batt_score + volt_score
            detail = {"battery_level": battery, "voltage": voltage}
        elif battery is not None:
            points = _linear_score(battery, BATTERY_LOW, BATTERY_FULL, WEIGHT_BATTERY)
            detail = {"battery_level": battery}
        else:
            points = _linear_score(voltage, VOLTAGE_MIN, VOLTAGE_HEALTHY, WEIGHT_BATTERY)
            detail = {"voltage": voltage}

        return (points, detail)

    def _score_signal(self, props: Dict[str, Any]) -> Optional[Tuple[float, Dict[str, Any]]]:
        """Score signal quality from SNR and hop distance."""
        snr = props.get("snr")
        hops = props.get("hops_away")

        if snr is None and hops is None:
            return None

        points = 0.0
        detail: Dict[str, Any] = {}

        if snr is not None:
            try:
                snr = float(snr)
            except (ValueError, TypeError):
                snr = None

        if hops is not None:
            try:
                hops = int(hops)
                hops = max(0, hops)
            except (ValueError, TypeError):
                hops = None

        # After conversion attempts, check if we still have any valid data
        if snr is None and hops is None:
            return None

        if snr is not None and hops is not None:
            # Both: 70% SNR, 30% hops
            snr_score = _linear_score(snr, SNR_POOR, SNR_EXCELLENT, WEIGHT_SIGNAL * 0.7)
            hop_score = _linear_score(
                MAX_HOPS_SCORED - hops, 0, MAX_HOPS_SCORED, WEIGHT_SIGNAL * 0.3,
            )
            points = snr_score + hop_score
            detail = {"snr": snr, "hops_away": hops}
        elif snr is not None:
            points = _linear_score(snr, SNR_POOR, SNR_EXCELLENT, WEIGHT_SIGNAL)
            detail = {"snr": snr}
        elif hops is not None:
            points = _linear_score(
                MAX_HOPS_SCORED - hops, 0, MAX_HOPS_SCORED, WEIGHT_SIGNAL,
            )
            detail = {"hops_away": hops}

        return (points, detail)

    def _score_freshness(
        self, props: Dict[str, Any], now: float
    ) -> Optional[Tuple[float, Dict[str, Any]]]:
        """Score data freshness from last_seen timestamp."""
        last_seen = props.get("last_seen")
        if last_seen is None:
            return None

        try:
            last_seen = float(last_seen)
        except (ValueError, TypeError):
            return None

        age = now - last_seen
        if age < 0:
            age = 0  # Clock skew protection

        points = _linear_score(
            self._freshness_stale - age,
            0,
            self._freshness_stale - self._freshness_fresh,
            WEIGHT_FRESHNESS,
        )
        detail = {"age_seconds": int(age)}
        return (points, detail)

    def _score_reliability(
        self, connectivity_state: Optional[str]
    ) -> Optional[Tuple[float, Dict[str, Any]]]:
        """Score reliability from NodeStateTracker connectivity state."""
        if connectivity_state is None:
            return None

        state_scores = {
            "stable": WEIGHT_RELIABILITY,
            "new": WEIGHT_RELIABILITY * 0.7,
            "intermittent": WEIGHT_RELIABILITY * 0.3,
            "offline": 0.0,
        }
        points = state_scores.get(connectivity_state, WEIGHT_RELIABILITY * 0.5)
        detail = {"connectivity_state": connectivity_state}
        return (points, detail)

    def _score_congestion(self, props: Dict[str, Any]) -> Optional[Tuple[float, Dict[str, Any]]]:
        """Score congestion from channel utilization and TX air time."""
        channel_util = props.get("channel_util")
        air_util_tx = props.get("air_util_tx")

        if channel_util is None and air_util_tx is None:
            return None

        detail: Dict[str, Any] = {}

        if channel_util is not None:
            try:
                channel_util = float(channel_util)
                channel_util = _clamp(channel_util, 0, 100)
            except (ValueError, TypeError):
                channel_util = None

        if air_util_tx is not None:
            try:
                air_util_tx = float(air_util_tx)
                air_util_tx = _clamp(air_util_tx, 0, 100)
            except (ValueError, TypeError):
                air_util_tx = None

        if channel_util is not None and air_util_tx is not None:
            # Both: average them, then invert (lower = better)
            avg_util = (channel_util + air_util_tx) / 2
            points = _linear_score(
                CHANNEL_UTIL_HIGH - avg_util,
                0,
                CHANNEL_UTIL_HIGH - CHANNEL_UTIL_LOW,
                WEIGHT_CONGESTION,
            )
            detail = {"channel_util": channel_util, "air_util_tx": air_util_tx}
        elif channel_util is not None:
            points = _linear_score(
                CHANNEL_UTIL_HIGH - channel_util,
                0,
                CHANNEL_UTIL_HIGH - CHANNEL_UTIL_LOW,
                WEIGHT_CONGESTION,
            )
            detail = {"channel_util": channel_util}
        else:
            points = _linear_score(
                CHANNEL_UTIL_HIGH - air_util_tx,
                0,
                CHANNEL_UTIL_HIGH - CHANNEL_UTIL_LOW,
                WEIGHT_CONGESTION,
            )
            detail = {"air_util_tx": air_util_tx}

        return (points, detail)

    def _evict_oldest_locked(self) -> None:
        """Evict the node with the oldest score timestamp. Must hold lock."""
        if not self._scores:
            return
        oldest_id = min(
            self._scores,
            key=lambda nid: self._scores[nid].timestamp,
        )
        del self._scores[oldest_id]
