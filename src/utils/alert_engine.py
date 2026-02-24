"""
MeshForge Maps - Alert Engine

Threshold-based alerting for mesh network monitoring. Evaluates configurable
rules against node telemetry and health data, generating alerts when thresholds
are crossed. Supports cooldown periods to prevent alert storms.

Alert types:
    node_offline     - Node has not been seen within threshold
    battery_low      - Battery level dropped below threshold
    battery_critical - Battery level dropped below critical threshold
    health_degraded  - Health score dropped below threshold
    congestion_high  - Channel utilization exceeded threshold
    signal_poor      - SNR dropped below threshold

Delivery:
    EventBus ALERT_FIRED events (published by map_server) for WebSocket
    broadcast to browser clients.  The engine itself only evaluates rules
    and records history — delivery is handled externally via the EventBus.

Thread-safe: all state behind a lock.
"""

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Maximum alerts to retain in history
MAX_ALERT_HISTORY = 500

# Cooldown cleanup: remove entries older than 24 hours
_COOLDOWN_MAX_AGE = 86400
_COOLDOWN_CLEANUP_INTERVAL = 3600

# Default cooldown per node+rule (seconds) — avoid re-firing same alert
DEFAULT_COOLDOWN = 600  # 10 minutes


class AlertSeverity(str, Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    """Built-in alert types."""
    NODE_OFFLINE = "node_offline"
    BATTERY_LOW = "battery_low"
    BATTERY_CRITICAL = "battery_critical"
    HEALTH_DEGRADED = "health_degraded"
    CONGESTION_HIGH = "congestion_high"
    SIGNAL_POOR = "signal_poor"


@dataclass
class AlertRule:
    """A threshold rule that generates alerts when conditions are met.

    Attributes:
        rule_id: Unique identifier for this rule
        alert_type: The type of alert this rule generates
        severity: Alert severity level
        metric: The property key to evaluate (e.g. "battery", "snr")
        operator: Comparison operator ("lt", "gt", "eq", "lte", "gte")
        threshold: Threshold value for comparison
        cooldown: Seconds before this rule can re-fire for the same node
        enabled: Whether this rule is active
        network_filter: Optional network filter (e.g. "meshtastic", "aredn")
        description: Human-readable description
    """
    rule_id: str
    alert_type: AlertType
    severity: AlertSeverity
    metric: str
    operator: str  # "lt", "gt", "eq", "lte", "gte"
    threshold: float
    cooldown: float = DEFAULT_COOLDOWN
    enabled: bool = True
    network_filter: Optional[str] = None
    description: str = ""

    def evaluate(self, value: float) -> bool:
        """Check if the value triggers this rule."""
        ops = {
            "lt": lambda v, t: v < t,
            "gt": lambda v, t: v > t,
            "eq": lambda v, t: v == t,
            "lte": lambda v, t: v <= t,
            "gte": lambda v, t: v >= t,
        }
        op_fn = ops.get(self.operator)
        if op_fn is None:
            return False
        return op_fn(value, self.threshold)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "alert_type": self.alert_type.value,
            "severity": self.severity.value,
            "metric": self.metric,
            "operator": self.operator,
            "threshold": self.threshold,
            "cooldown": self.cooldown,
            "enabled": self.enabled,
            "network_filter": self.network_filter,
            "description": self.description,
        }


@dataclass
class Alert:
    """A generated alert instance."""
    alert_id: str
    rule_id: str
    alert_type: str
    severity: str
    node_id: str
    metric: str
    value: float
    threshold: float
    message: str
    timestamp: float = field(default_factory=time.time)
    acknowledged: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "rule_id": self.rule_id,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "node_id": self.node_id,
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
            "message": self.message,
            "timestamp": self.timestamp,
            "acknowledged": self.acknowledged,
        }


# Default rules for common alert scenarios
DEFAULT_RULES = [
    AlertRule(
        rule_id="battery_low",
        alert_type=AlertType.BATTERY_LOW,
        severity=AlertSeverity.WARNING,
        metric="battery",
        operator="lte",
        threshold=20.0,
        description="Battery level is low (<=20%)",
    ),
    AlertRule(
        rule_id="battery_critical",
        alert_type=AlertType.BATTERY_CRITICAL,
        severity=AlertSeverity.CRITICAL,
        metric="battery",
        operator="lte",
        threshold=5.0,
        description="Battery level is critical (<=5%)",
    ),
    AlertRule(
        rule_id="signal_poor",
        alert_type=AlertType.SIGNAL_POOR,
        severity=AlertSeverity.WARNING,
        metric="snr",
        operator="lte",
        threshold=-10.0,
        description="Signal quality is poor (SNR <= -10 dB)",
    ),
    AlertRule(
        rule_id="congestion_high",
        alert_type=AlertType.CONGESTION_HIGH,
        severity=AlertSeverity.WARNING,
        metric="channel_util",
        operator="gte",
        threshold=75.0,
        description="Channel utilization is high (>=75%)",
    ),
    AlertRule(
        rule_id="health_degraded",
        alert_type=AlertType.HEALTH_DEGRADED,
        severity=AlertSeverity.WARNING,
        metric="health_score",
        operator="lte",
        threshold=20.0,
        description="Node health score is critical (<=20)",
    ),
]


class AlertEngine:
    """Threshold-based alert engine for mesh node monitoring.

    Evaluates rules against node properties and health data, generates
    alerts with cooldown throttling, and maintains bounded alert history.
    Alert delivery is handled externally via the EventBus (ALERT_FIRED
    events published by the map server).

    Usage:
        engine = AlertEngine()
        alerts = engine.evaluate_node("!a1b2c3d4", node_props, health_score=45)
    """

    def __init__(
        self,
        rules: Optional[List[AlertRule]] = None,
        max_history: int = MAX_ALERT_HISTORY,
    ):
        self._lock = threading.Lock()
        self._rules: Dict[str, AlertRule] = {}
        self._history: List[Alert] = []
        self._max_history = max_history
        self._cooldowns: Dict[str, float] = {}  # "node_id:rule_id" -> last_fired
        self._alert_counter = 0
        self._total_alerts_fired = 0
        self._last_cooldown_cleanup: float = 0.0

        # Load rules — copy defaults to avoid shared mutation
        source = rules if rules is not None else DEFAULT_RULES
        for rule in source:
            self._rules[rule.rule_id] = copy.copy(rule)

    def add_rule(self, rule: AlertRule) -> None:
        """Add or replace an alert rule."""
        with self._lock:
            self._rules[rule.rule_id] = rule

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by ID. Returns True if removed."""
        with self._lock:
            return self._rules.pop(rule_id, None) is not None

    def get_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Get a rule by ID."""
        with self._lock:
            rule = self._rules.get(rule_id)
            return rule.to_dict() if rule else None

    def list_rules(self) -> List[Dict[str, Any]]:
        """List all configured rules."""
        with self._lock:
            return [r.to_dict() for r in self._rules.values()]

    def enable_rule(self, rule_id: str) -> bool:
        """Enable a rule. Returns True if found."""
        with self._lock:
            rule = self._rules.get(rule_id)
            if rule:
                rule.enabled = True
                return True
            return False

    def disable_rule(self, rule_id: str) -> bool:
        """Disable a rule. Returns True if found."""
        with self._lock:
            rule = self._rules.get(rule_id)
            if rule:
                rule.enabled = False
                return True
            return False

    def evaluate_node(
        self,
        node_id: str,
        props: Dict[str, Any],
        health_score: Optional[int] = None,
        now: Optional[float] = None,
    ) -> List[Alert]:
        """Evaluate all rules against a node's properties.

        Args:
            node_id: Node identifier
            props: GeoJSON feature properties dict
            health_score: Optional health score (0-100) from NodeHealthScorer
            now: Current timestamp (defaults to time.time())

        Returns:
            List of Alert objects that were triggered
        """
        if now is None:
            now = time.time()

        # Periodic cleanup of stale cooldown entries
        if now - self._last_cooldown_cleanup > _COOLDOWN_CLEANUP_INTERVAL:
            self._cleanup_stale_cooldowns(now)

        # Build evaluation context: node props + health score
        context = dict(props)
        if health_score is not None:
            context["health_score"] = health_score

        network = props.get("network")
        triggered: List[Alert] = []

        with self._lock:
            rules = list(self._rules.values())

        for rule in rules:
            if not rule.enabled:
                continue

            # Network filter check
            if rule.network_filter and network != rule.network_filter:
                continue

            # Get metric value from context
            value = context.get(rule.metric)
            if value is None:
                continue

            try:
                value = float(value)
            except (ValueError, TypeError):
                continue

            if not rule.evaluate(value):
                continue

            # Cooldown check
            cooldown_key = f"{node_id}:{rule.rule_id}"
            with self._lock:
                last_fired = self._cooldowns.get(cooldown_key, 0)
                if now - last_fired < rule.cooldown:
                    continue

                # Generate alert
                self._alert_counter += 1
                alert_id = f"alert-{self._alert_counter}"
                alert = Alert(
                    alert_id=alert_id,
                    rule_id=rule.rule_id,
                    alert_type=rule.alert_type.value,
                    severity=rule.severity.value,
                    node_id=node_id,
                    metric=rule.metric,
                    value=value,
                    threshold=rule.threshold,
                    message=f"{rule.description} — node {node_id}: "
                            f"{rule.metric}={value}",
                    timestamp=now,
                )

                self._cooldowns[cooldown_key] = now
                self._history.append(alert)
                self._total_alerts_fired += 1

                # Trim history if needed
                if len(self._history) > self._max_history:
                    self._history = self._history[-self._max_history:]

            triggered.append(alert)

        return triggered

    def evaluate_offline(
        self,
        node_id: str,
        last_seen: float,
        offline_threshold: float = 3600.0,
        now: Optional[float] = None,
    ) -> Optional[Alert]:
        """Check if a node should trigger an offline alert.

        This is separate from evaluate_node because offline detection
        doesn't come from node properties — it comes from absence.

        Args:
            node_id: Node identifier
            last_seen: Timestamp of last observation
            offline_threshold: Seconds before considering offline
            now: Current timestamp

        Returns:
            Alert if triggered, None otherwise
        """
        if now is None:
            now = time.time()

        age = now - last_seen
        if age <= offline_threshold:
            return None

        cooldown_key = f"{node_id}:node_offline"
        with self._lock:
            last_fired = self._cooldowns.get(cooldown_key, 0)
            if now - last_fired < DEFAULT_COOLDOWN:
                return None

            self._alert_counter += 1
            alert = Alert(
                alert_id=f"alert-{self._alert_counter}",
                rule_id="node_offline",
                alert_type=AlertType.NODE_OFFLINE.value,
                severity=AlertSeverity.CRITICAL.value,
                node_id=node_id,
                metric="seconds_since_seen",
                value=age,
                threshold=offline_threshold,
                message=f"Node {node_id} offline — last seen {int(age)}s ago",
                timestamp=now,
            )
            self._cooldowns[cooldown_key] = now
            self._history.append(alert)
            self._total_alerts_fired += 1

            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

        return alert

    def _cleanup_stale_cooldowns(self, now: float) -> None:
        """Remove cooldown entries older than _COOLDOWN_MAX_AGE (24h)."""
        with self._lock:
            stale = [k for k, t in self._cooldowns.items()
                     if now - t > _COOLDOWN_MAX_AGE]
            for k in stale:
                del self._cooldowns[k]
            self._last_cooldown_cleanup = now

    def acknowledge(self, alert_id: str) -> bool:
        """Acknowledge an alert by ID. Returns True if found."""
        with self._lock:
            for alert in self._history:
                if alert.alert_id == alert_id:
                    alert.acknowledged = True
                    return True
        return False

    def get_active_alerts(self) -> List[Dict[str, Any]]:
        """Return all unacknowledged alerts."""
        with self._lock:
            return [
                a.to_dict() for a in self._history
                if not a.acknowledged
            ]

    def get_alert_history(
        self,
        limit: int = 50,
        severity: Optional[str] = None,
        node_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return recent alert history with optional filters."""
        with self._lock:
            alerts = list(self._history)

        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        if node_id:
            alerts = [a for a in alerts if a.node_id == node_id]

        # Most recent first
        alerts.reverse()
        return [a.to_dict() for a in alerts[:limit]]

    def get_summary(self) -> Dict[str, Any]:
        """Return alert summary statistics."""
        with self._lock:
            active = sum(1 for a in self._history if not a.acknowledged)
            by_severity: Dict[str, int] = {}
            by_type: Dict[str, int] = {}
            for a in self._history:
                if not a.acknowledged:
                    by_severity[a.severity] = by_severity.get(a.severity, 0) + 1
                    by_type[a.alert_type] = by_type.get(a.alert_type, 0) + 1

            return {
                "total_rules": len(self._rules),
                "enabled_rules": sum(1 for r in self._rules.values() if r.enabled),
                "total_alerts_fired": self._total_alerts_fired,
                "active_alerts": active,
                "history_size": len(self._history),
                "by_severity": by_severity,
                "by_type": by_type,
            }

    def clear_cooldowns(self) -> None:
        """Clear all cooldown timers (useful for testing)."""
        with self._lock:
            self._cooldowns.clear()
