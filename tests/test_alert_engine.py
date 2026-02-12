"""Tests for the alert engine — threshold evaluation, cooldown, history, delivery."""

import time
from unittest.mock import MagicMock, patch

import pytest

from src.utils.alert_engine import (
    Alert,
    AlertEngine,
    AlertRule,
    AlertSeverity,
    AlertType,
    DEFAULT_COOLDOWN,
    DEFAULT_RULES,
    MAX_ALERT_HISTORY,
)


# ---------------------------------------------------------------------------
# AlertRule.evaluate
# ---------------------------------------------------------------------------

class TestAlertRuleEvaluate:
    def test_lt_operator(self):
        rule = AlertRule(
            rule_id="test", alert_type=AlertType.BATTERY_LOW,
            severity=AlertSeverity.WARNING, metric="battery",
            operator="lt", threshold=20.0,
        )
        assert rule.evaluate(10.0) is True
        assert rule.evaluate(20.0) is False
        assert rule.evaluate(30.0) is False

    def test_gt_operator(self):
        rule = AlertRule(
            rule_id="test", alert_type=AlertType.CONGESTION_HIGH,
            severity=AlertSeverity.WARNING, metric="channel_util",
            operator="gt", threshold=75.0,
        )
        assert rule.evaluate(80.0) is True
        assert rule.evaluate(75.0) is False
        assert rule.evaluate(50.0) is False

    def test_eq_operator(self):
        rule = AlertRule(
            rule_id="test", alert_type=AlertType.BATTERY_LOW,
            severity=AlertSeverity.INFO, metric="battery",
            operator="eq", threshold=50.0,
        )
        assert rule.evaluate(50.0) is True
        assert rule.evaluate(49.0) is False

    def test_lte_operator(self):
        rule = AlertRule(
            rule_id="test", alert_type=AlertType.BATTERY_LOW,
            severity=AlertSeverity.WARNING, metric="battery",
            operator="lte", threshold=20.0,
        )
        assert rule.evaluate(20.0) is True
        assert rule.evaluate(19.0) is True
        assert rule.evaluate(21.0) is False

    def test_gte_operator(self):
        rule = AlertRule(
            rule_id="test", alert_type=AlertType.CONGESTION_HIGH,
            severity=AlertSeverity.WARNING, metric="channel_util",
            operator="gte", threshold=75.0,
        )
        assert rule.evaluate(75.0) is True
        assert rule.evaluate(76.0) is True
        assert rule.evaluate(74.0) is False

    def test_invalid_operator_returns_false(self):
        rule = AlertRule(
            rule_id="test", alert_type=AlertType.BATTERY_LOW,
            severity=AlertSeverity.WARNING, metric="battery",
            operator="invalid", threshold=20.0,
        )
        assert rule.evaluate(10.0) is False

    def test_to_dict(self):
        rule = AlertRule(
            rule_id="test", alert_type=AlertType.BATTERY_LOW,
            severity=AlertSeverity.WARNING, metric="battery",
            operator="lte", threshold=20.0,
            description="Test rule",
        )
        d = rule.to_dict()
        assert d["rule_id"] == "test"
        assert d["alert_type"] == "battery_low"
        assert d["severity"] == "warning"
        assert d["metric"] == "battery"
        assert d["operator"] == "lte"
        assert d["threshold"] == 20.0
        assert d["description"] == "Test rule"
        assert d["enabled"] is True


# ---------------------------------------------------------------------------
# Alert dataclass
# ---------------------------------------------------------------------------

class TestAlert:
    def test_to_dict(self):
        alert = Alert(
            alert_id="alert-1", rule_id="battery_low",
            alert_type="battery_low", severity="warning",
            node_id="!abc123", metric="battery", value=15.0,
            threshold=20.0, message="Battery low",
            timestamp=1000.0,
        )
        d = alert.to_dict()
        assert d["alert_id"] == "alert-1"
        assert d["node_id"] == "!abc123"
        assert d["value"] == 15.0
        assert d["acknowledged"] is False


# ---------------------------------------------------------------------------
# AlertEngine — rule management
# ---------------------------------------------------------------------------

class TestAlertEngineRules:
    def test_default_rules_loaded(self):
        engine = AlertEngine()
        rules = engine.list_rules()
        assert len(rules) == len(DEFAULT_RULES)

    def test_custom_rules_override_defaults(self):
        custom = [AlertRule(
            rule_id="custom", alert_type=AlertType.BATTERY_LOW,
            severity=AlertSeverity.INFO, metric="battery",
            operator="lte", threshold=50.0,
        )]
        engine = AlertEngine(rules=custom)
        rules = engine.list_rules()
        assert len(rules) == 1
        assert rules[0]["rule_id"] == "custom"

    def test_empty_rules_list(self):
        engine = AlertEngine(rules=[])
        assert engine.list_rules() == []

    def test_add_rule(self):
        engine = AlertEngine(rules=[])
        rule = AlertRule(
            rule_id="new", alert_type=AlertType.SIGNAL_POOR,
            severity=AlertSeverity.WARNING, metric="snr",
            operator="lte", threshold=-5.0,
        )
        engine.add_rule(rule)
        assert len(engine.list_rules()) == 1

    def test_remove_rule(self):
        engine = AlertEngine()
        count_before = len(engine.list_rules())
        assert engine.remove_rule("battery_low") is True
        assert len(engine.list_rules()) == count_before - 1
        assert engine.remove_rule("nonexistent") is False

    def test_get_rule(self):
        engine = AlertEngine()
        rule = engine.get_rule("battery_low")
        assert rule is not None
        assert rule["rule_id"] == "battery_low"
        assert engine.get_rule("nonexistent") is None

    def test_enable_disable_rule(self):
        engine = AlertEngine()
        assert engine.disable_rule("battery_low") is True
        rule = engine.get_rule("battery_low")
        assert rule["enabled"] is False

        assert engine.enable_rule("battery_low") is True
        rule = engine.get_rule("battery_low")
        assert rule["enabled"] is True

        assert engine.disable_rule("nonexistent") is False
        assert engine.enable_rule("nonexistent") is False


# ---------------------------------------------------------------------------
# AlertEngine — node evaluation
# ---------------------------------------------------------------------------

class TestAlertEngineEvaluation:
    def test_battery_low_triggers(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        alerts = engine.evaluate_node("!abc", {"battery": 15}, now=1000.0)
        # Should trigger battery_low and battery_critical
        alert_types = [a.alert_type for a in alerts]
        assert "battery_low" in alert_types

    def test_battery_critical_triggers(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        alerts = engine.evaluate_node("!abc", {"battery": 3}, now=1000.0)
        alert_types = [a.alert_type for a in alerts]
        assert "battery_critical" in alert_types

    def test_healthy_node_no_alerts(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        alerts = engine.evaluate_node("!abc", {"battery": 80, "snr": 10.0}, now=1000.0)
        assert len(alerts) == 0

    def test_signal_poor_triggers(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        alerts = engine.evaluate_node("!abc", {"snr": -15.0}, now=1000.0)
        alert_types = [a.alert_type for a in alerts]
        assert "signal_poor" in alert_types

    def test_congestion_high_triggers(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        alerts = engine.evaluate_node("!abc", {"channel_util": 80.0}, now=1000.0)
        alert_types = [a.alert_type for a in alerts]
        assert "congestion_high" in alert_types

    def test_health_degraded_triggers(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        alerts = engine.evaluate_node("!abc", {}, health_score=15, now=1000.0)
        alert_types = [a.alert_type for a in alerts]
        assert "health_degraded" in alert_types

    def test_missing_metric_skipped(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        # No battery, snr, channel_util, or health_score
        alerts = engine.evaluate_node("!abc", {"network": "meshtastic"}, now=1000.0)
        assert len(alerts) == 0

    def test_non_numeric_metric_skipped(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        alerts = engine.evaluate_node("!abc", {"battery": "not_a_number"}, now=1000.0)
        assert len(alerts) == 0

    def test_network_filter(self):
        rule = AlertRule(
            rule_id="aredn_only", alert_type=AlertType.SIGNAL_POOR,
            severity=AlertSeverity.WARNING, metric="snr",
            operator="lte", threshold=-5.0,
            network_filter="aredn",
        )
        engine = AlertEngine(rules=[rule])
        engine.clear_cooldowns()

        # Should not trigger for meshtastic
        alerts = engine.evaluate_node("!abc", {"snr": -10.0, "network": "meshtastic"}, now=1000.0)
        assert len(alerts) == 0

        # Should trigger for aredn
        alerts = engine.evaluate_node("!abc", {"snr": -10.0, "network": "aredn"}, now=2000.0)
        assert len(alerts) == 1

    def test_disabled_rule_skipped(self):
        engine = AlertEngine()
        engine.disable_rule("battery_low")
        engine.clear_cooldowns()
        # battery=3 triggers both battery_low and battery_critical thresholds
        alerts = engine.evaluate_node("!abc", {"battery": 3}, now=1000.0)
        alert_types = [a.alert_type for a in alerts]
        # battery_low is disabled, should not fire
        assert "battery_low" not in alert_types
        # battery_critical should still fire (3 <= 5)
        assert "battery_critical" in alert_types


# ---------------------------------------------------------------------------
# AlertEngine — cooldown
# ---------------------------------------------------------------------------

class TestAlertEngineCooldown:
    def test_cooldown_prevents_duplicate_alert(self):
        engine = AlertEngine()
        engine.clear_cooldowns()

        alerts1 = engine.evaluate_node("!abc", {"battery": 15}, now=1000.0)
        assert len(alerts1) > 0

        # Same evaluation within cooldown period — should not re-trigger
        alerts2 = engine.evaluate_node("!abc", {"battery": 15}, now=1100.0)
        assert len(alerts2) == 0

    def test_cooldown_expires(self):
        rule = AlertRule(
            rule_id="fast", alert_type=AlertType.BATTERY_LOW,
            severity=AlertSeverity.WARNING, metric="battery",
            operator="lte", threshold=20.0,
            cooldown=60.0,
        )
        engine = AlertEngine(rules=[rule])
        engine.clear_cooldowns()

        alerts1 = engine.evaluate_node("!abc", {"battery": 15}, now=1000.0)
        assert len(alerts1) == 1

        # Within cooldown
        alerts2 = engine.evaluate_node("!abc", {"battery": 15}, now=1050.0)
        assert len(alerts2) == 0

        # After cooldown
        alerts3 = engine.evaluate_node("!abc", {"battery": 15}, now=1061.0)
        assert len(alerts3) == 1

    def test_different_nodes_independent_cooldowns(self):
        engine = AlertEngine()
        engine.clear_cooldowns()

        alerts1 = engine.evaluate_node("!abc", {"battery": 15}, now=1000.0)
        assert len(alerts1) > 0

        # Different node — should trigger independently
        alerts2 = engine.evaluate_node("!def", {"battery": 15}, now=1000.0)
        assert len(alerts2) > 0

    def test_clear_cooldowns(self):
        engine = AlertEngine()
        engine.clear_cooldowns()

        engine.evaluate_node("!abc", {"battery": 15}, now=1000.0)
        engine.clear_cooldowns()

        # After clearing, should fire again
        alerts = engine.evaluate_node("!abc", {"battery": 15}, now=1001.0)
        assert len(alerts) > 0


# ---------------------------------------------------------------------------
# AlertEngine — offline detection
# ---------------------------------------------------------------------------

class TestAlertEngineOffline:
    def test_offline_alert(self):
        engine = AlertEngine()
        engine.clear_cooldowns()

        alert = engine.evaluate_offline(
            "!abc", last_seen=0.0, offline_threshold=3600.0, now=5000.0,
        )
        assert alert is not None
        assert alert.alert_type == "node_offline"
        assert alert.severity == "critical"
        assert alert.node_id == "!abc"

    def test_not_offline_yet(self):
        engine = AlertEngine()
        engine.clear_cooldowns()

        alert = engine.evaluate_offline(
            "!abc", last_seen=4000.0, offline_threshold=3600.0, now=5000.0,
        )
        assert alert is None

    def test_offline_cooldown(self):
        engine = AlertEngine()
        engine.clear_cooldowns()

        alert1 = engine.evaluate_offline("!abc", last_seen=0.0, now=5000.0)
        assert alert1 is not None

        # Within cooldown — should not re-fire
        alert2 = engine.evaluate_offline("!abc", last_seen=0.0, now=5100.0)
        assert alert2 is None


# ---------------------------------------------------------------------------
# AlertEngine — history and acknowledgment
# ---------------------------------------------------------------------------

class TestAlertEngineHistory:
    def test_alert_history(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        engine.evaluate_node("!abc", {"battery": 3}, now=1000.0)
        history = engine.get_alert_history(limit=50)
        assert len(history) > 0
        assert history[0]["node_id"] == "!abc"

    def test_history_limit(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        engine.evaluate_node("!abc", {"battery": 3}, now=1000.0)
        history = engine.get_alert_history(limit=1)
        assert len(history) <= 1

    def test_history_severity_filter(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        engine.evaluate_node("!abc", {"battery": 3}, now=1000.0)
        warnings = engine.get_alert_history(severity="warning")
        criticals = engine.get_alert_history(severity="critical")
        for a in warnings:
            assert a["severity"] == "warning"
        for a in criticals:
            assert a["severity"] == "critical"

    def test_history_node_filter(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        engine.evaluate_node("!abc", {"battery": 3}, now=1000.0)
        engine.evaluate_node("!def", {"battery": 3}, now=2000.0)
        history = engine.get_alert_history(node_id="!abc")
        for a in history:
            assert a["node_id"] == "!abc"

    def test_acknowledge_alert(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        alerts = engine.evaluate_node("!abc", {"battery": 3}, now=1000.0)
        assert len(alerts) > 0

        alert_id = alerts[0].alert_id
        assert engine.acknowledge(alert_id) is True
        assert engine.acknowledge("nonexistent") is False

        active = engine.get_active_alerts()
        acked_ids = {a["alert_id"] for a in active}
        assert alert_id not in acked_ids

    def test_active_alerts(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        alerts = engine.evaluate_node("!abc", {"battery": 3}, now=1000.0)
        active = engine.get_active_alerts()
        assert len(active) == len(alerts)

        # Acknowledge all
        for a in alerts:
            engine.acknowledge(a.alert_id)
        active = engine.get_active_alerts()
        assert len(active) == 0

    def test_history_bounded(self):
        """History should not exceed MAX_ALERT_HISTORY."""
        rule = AlertRule(
            rule_id="fast", alert_type=AlertType.BATTERY_LOW,
            severity=AlertSeverity.WARNING, metric="battery",
            operator="lte", threshold=100.0,  # always triggers
            cooldown=0,  # no cooldown for this test
        )
        engine = AlertEngine(rules=[rule], max_history=10)

        for i in range(20):
            engine.evaluate_node(f"!node{i}", {"battery": 50}, now=float(i))

        history = engine.get_alert_history(limit=100)
        assert len(history) <= 10


# ---------------------------------------------------------------------------
# AlertEngine — summary
# ---------------------------------------------------------------------------

class TestAlertEngineSummary:
    def test_summary_structure(self):
        engine = AlertEngine()
        summary = engine.get_summary()
        assert "total_rules" in summary
        assert "enabled_rules" in summary
        assert "total_alerts_fired" in summary
        assert "active_alerts" in summary
        assert "history_size" in summary
        assert "by_severity" in summary
        assert "by_type" in summary

    def test_summary_counts(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        engine.evaluate_node("!abc", {"battery": 3}, now=1000.0)
        summary = engine.get_summary()
        assert summary["total_alerts_fired"] > 0
        assert summary["active_alerts"] > 0


# ---------------------------------------------------------------------------
# AlertEngine — delivery
# ---------------------------------------------------------------------------

class TestAlertEngineDelivery:
    def test_on_alert_callback(self):
        received = []
        engine = AlertEngine(on_alert=received.append)
        engine.clear_cooldowns()
        engine.evaluate_node("!abc", {"battery": 3}, now=1000.0)
        assert len(received) > 0
        assert all(isinstance(a, Alert) for a in received)

    def test_callback_error_does_not_break(self):
        def bad_callback(alert):
            raise RuntimeError("boom")

        engine = AlertEngine(on_alert=bad_callback)
        engine.clear_cooldowns()
        # Should not raise
        alerts = engine.evaluate_node("!abc", {"battery": 3}, now=1000.0)
        assert len(alerts) > 0

    def test_webhook_management(self):
        engine = AlertEngine()
        engine.add_webhook("https://example.com/hook1")
        engine.add_webhook("https://example.com/hook2")
        assert len(engine.list_webhooks()) == 2

        # Duplicate not added
        engine.add_webhook("https://example.com/hook1")
        assert len(engine.list_webhooks()) == 2

        assert engine.remove_webhook("https://example.com/hook1") is True
        assert len(engine.list_webhooks()) == 1
        assert engine.remove_webhook("nonexistent") is False

    @patch("src.utils.alert_engine.urllib.request.urlopen")
    def test_webhook_delivery(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        engine = AlertEngine()
        engine.add_webhook("https://example.com/hook")
        engine.clear_cooldowns()
        engine.evaluate_node("!abc", {"battery": 3}, now=1000.0)
        assert mock_urlopen.called

    @patch("src.utils.alert_engine.urllib.request.urlopen")
    def test_webhook_failure_does_not_break(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("network error")

        engine = AlertEngine()
        engine.add_webhook("https://example.com/hook")
        engine.clear_cooldowns()
        # Should not raise
        alerts = engine.evaluate_node("!abc", {"battery": 3}, now=1000.0)
        assert len(alerts) > 0


# ---------------------------------------------------------------------------
# AlertEngine — offline alert delivery
# ---------------------------------------------------------------------------

class TestOfflineAlertDelivery:
    def test_offline_alert_calls_callback(self):
        received = []
        engine = AlertEngine(on_alert=received.append)
        engine.clear_cooldowns()
        engine.evaluate_offline("!abc", last_seen=0.0, now=5000.0)
        assert len(received) == 1
        assert received[0].alert_type == "node_offline"


# ---------------------------------------------------------------------------
# AlertEngine — alert message format
# ---------------------------------------------------------------------------

class TestAlertMessage:
    def test_alert_message_contains_node_and_metric(self):
        engine = AlertEngine()
        engine.clear_cooldowns()
        alerts = engine.evaluate_node("!abc123", {"battery": 10}, now=1000.0)
        for a in alerts:
            assert "!abc123" in a.message
            assert a.metric in a.message


# ---------------------------------------------------------------------------
# Integration: MapServer alert routes
# ---------------------------------------------------------------------------

class TestMapServerAlertRoutes:
    def _make_server(self, tmp_path):
        from src.utils.config import MapsConfig
        from src.map_server import MapServer
        config = MapsConfig(config_path=tmp_path / "settings.json")
        config.set("enable_meshtastic", False)
        config.set("enable_reticulum", False)
        config.set("enable_hamclock", False)
        config.set("enable_aredn", False)
        return MapServer(config)

    def test_server_has_alert_engine(self, tmp_path):
        server = self._make_server(tmp_path)
        assert server.alert_engine is not None
        assert isinstance(server.alert_engine, AlertEngine)

    def test_alert_api_rules(self, tmp_path):
        import urllib.request
        import json

        server = self._make_server(tmp_path)
        try:
            server.start()
            url = f"http://127.0.0.1:{server.port}/api/alerts/rules"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
                assert "rules" in data
                assert len(data["rules"]) == len(DEFAULT_RULES)
        finally:
            server.stop()

    def test_alert_api_active(self, tmp_path):
        import urllib.request
        import json

        server = self._make_server(tmp_path)
        try:
            server.start()
            url = f"http://127.0.0.1:{server.port}/api/alerts/active"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
                assert "alerts" in data
                assert isinstance(data["alerts"], list)
        finally:
            server.stop()

    def test_alert_api_summary(self, tmp_path):
        import urllib.request
        import json

        server = self._make_server(tmp_path)
        try:
            server.start()
            url = f"http://127.0.0.1:{server.port}/api/alerts/summary"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
                assert "total_rules" in data
                assert "active_alerts" in data
        finally:
            server.stop()

    def test_alert_api_history(self, tmp_path):
        import urllib.request
        import json

        server = self._make_server(tmp_path)
        try:
            server.start()
            url = f"http://127.0.0.1:{server.port}/api/alerts"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
                assert "alerts" in data
                assert isinstance(data["alerts"], list)
        finally:
            server.stop()

    def test_status_includes_alerts(self, tmp_path):
        import urllib.request
        import json

        server = self._make_server(tmp_path)
        try:
            server.start()
            url = f"http://127.0.0.1:{server.port}/api/status"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
                assert "alerts" in data
                assert "total_rules" in data["alerts"]
        finally:
            server.stop()
