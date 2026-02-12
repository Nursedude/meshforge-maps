"""Tests for alerting delivery expansion: MQTT publish, EventBus propagation."""

import json
import time
from unittest.mock import MagicMock, patch, call

import pytest

from src.utils.alert_engine import (
    Alert,
    AlertEngine,
    AlertRule,
    AlertSeverity,
    AlertType,
    DEFAULT_COOLDOWN,
)
from src.utils.event_bus import Event, EventBus, EventType, NodeEvent


# ---------------------------------------------------------------------------
# MQTT alert publishing
# ---------------------------------------------------------------------------


class TestMQTTAlertPublishing:
    """Tests for MQTT publish delivery channel in AlertEngine."""

    def _make_engine(self, mqtt_client=None, mqtt_topic="meshforge/alerts"):
        return AlertEngine(
            mqtt_client=mqtt_client,
            mqtt_topic=mqtt_topic,
        )

    def test_mqtt_publish_on_alert(self):
        """Alert triggers MQTT publish to base and severity topics."""
        client = MagicMock()
        engine = self._make_engine(mqtt_client=client)
        engine.clear_cooldowns()

        alerts = engine.evaluate_node(
            "!abc123",
            {"battery": 3, "network": "meshtastic"},
            now=time.time(),
        )

        # Should trigger battery_critical (<=5) and battery_low (<=20)
        assert len(alerts) >= 1
        assert client.publish.called

        # Check that publish was called with the correct topic pattern
        published_topics = [c[0][0] for c in client.publish.call_args_list]
        # Base topic should be present
        assert "meshforge/alerts" in published_topics
        # Severity sub-topic should be present
        severity_topics = [t for t in published_topics if "/" in t and t != "meshforge/alerts"]
        assert len(severity_topics) >= 1

    def test_mqtt_publish_payload_is_valid_json(self):
        """MQTT publish payload should be valid JSON of the alert dict."""
        client = MagicMock()
        engine = self._make_engine(mqtt_client=client)
        engine.clear_cooldowns()

        engine.evaluate_node(
            "!abc123",
            {"battery": 3, "network": "meshtastic"},
        )

        # Extract the payload from the first publish call
        payload = client.publish.call_args_list[0][0][1]
        parsed = json.loads(payload)
        assert "alert_id" in parsed
        assert "severity" in parsed
        assert "node_id" in parsed
        assert parsed["node_id"] == "!abc123"

    def test_mqtt_publish_with_qos1(self):
        """MQTT alerts should be published with QoS 1."""
        client = MagicMock()
        engine = self._make_engine(mqtt_client=client)
        engine.clear_cooldowns()

        engine.evaluate_node(
            "!abc123",
            {"battery": 3, "network": "meshtastic"},
        )

        for call_args in client.publish.call_args_list:
            assert call_args[1].get("qos", call_args[0][2] if len(call_args[0]) > 2 else None) == 1 or \
                   len(call_args[0]) >= 3 and call_args[0][2] == 1

    def test_mqtt_severity_subtopic(self):
        """Alerts publish to severity-specific sub-topic."""
        client = MagicMock()
        engine = self._make_engine(mqtt_client=client)
        engine.clear_cooldowns()

        engine.evaluate_node(
            "!abc123",
            {"battery": 3, "network": "meshtastic"},
        )

        published_topics = [c[0][0] for c in client.publish.call_args_list]
        # battery_critical is severity=critical
        assert "meshforge/alerts/critical" in published_topics

    def test_mqtt_custom_topic(self):
        """Custom MQTT topic prefix is used."""
        client = MagicMock()
        engine = self._make_engine(
            mqtt_client=client,
            mqtt_topic="custom/alerts/topic",
        )
        engine.clear_cooldowns()

        engine.evaluate_node(
            "!abc123",
            {"battery": 3, "network": "meshtastic"},
        )

        published_topics = [c[0][0] for c in client.publish.call_args_list]
        assert "custom/alerts/topic" in published_topics

    def test_no_mqtt_when_client_is_none(self):
        """No MQTT publish when client is not configured."""
        engine = self._make_engine(mqtt_client=None)
        engine.clear_cooldowns()

        # Should not raise
        alerts = engine.evaluate_node(
            "!abc123",
            {"battery": 3, "network": "meshtastic"},
        )
        assert len(alerts) >= 1

    def test_mqtt_publish_error_is_swallowed(self):
        """MQTT publish errors are logged but don't crash."""
        client = MagicMock()
        client.publish.side_effect = Exception("MQTT connection lost")
        engine = self._make_engine(mqtt_client=client)
        engine.clear_cooldowns()

        # Should not raise
        alerts = engine.evaluate_node(
            "!abc123",
            {"battery": 3, "network": "meshtastic"},
        )
        assert len(alerts) >= 1

    def test_set_mqtt_client(self):
        """set_mqtt_client configures the MQTT client dynamically."""
        engine = AlertEngine()
        assert engine.mqtt_publish_count == 0

        client = MagicMock()
        engine.set_mqtt_client(client, "test/alerts")
        engine.clear_cooldowns()

        engine.evaluate_node("!abc", {"battery": 3})
        assert client.publish.called

    def test_remove_mqtt_client(self):
        """remove_mqtt_client disables MQTT publishing."""
        client = MagicMock()
        engine = self._make_engine(mqtt_client=client)
        engine.remove_mqtt_client()
        engine.clear_cooldowns()

        engine.evaluate_node("!abc", {"battery": 3})
        assert not client.publish.called

    def test_mqtt_publish_count_tracks(self):
        """mqtt_publish_count increments on successful publishes."""
        client = MagicMock()
        engine = self._make_engine(mqtt_client=client)
        engine.clear_cooldowns()

        assert engine.mqtt_publish_count == 0
        engine.evaluate_node("!abc", {"battery": 3})
        assert engine.mqtt_publish_count > 0

    def test_summary_includes_mqtt_stats(self):
        """Alert summary includes mqtt_enabled and mqtt_publish_count."""
        client = MagicMock()
        engine = self._make_engine(mqtt_client=client)

        summary = engine.get_summary()
        assert "mqtt_enabled" in summary
        assert summary["mqtt_enabled"] is True
        assert "mqtt_publish_count" in summary

    def test_summary_mqtt_disabled(self):
        """Alert summary shows mqtt_enabled=False when no client."""
        engine = AlertEngine()
        summary = engine.get_summary()
        assert summary["mqtt_enabled"] is False

    def test_offline_alert_publishes_mqtt(self):
        """evaluate_offline triggers MQTT publish for offline alerts."""
        client = MagicMock()
        engine = self._make_engine(mqtt_client=client)

        now = time.time()
        alert = engine.evaluate_offline(
            "!offline1", last_seen=now - 7200, now=now,
        )
        assert alert is not None
        assert client.publish.called


# ---------------------------------------------------------------------------
# EventBus alert propagation
# ---------------------------------------------------------------------------


class TestEventBusAlertPropagation:
    """Tests for ALERT_FIRED event propagation via EventBus."""

    def test_alert_fired_event_published(self):
        """Alerts fire ALERT_FIRED events on the EventBus."""
        bus = EventBus()
        received = []
        bus.subscribe(EventType.ALERT_FIRED, lambda e: received.append(e))

        # Simulate what map_server._handle_telemetry_for_alerts does
        engine = AlertEngine()
        engine.clear_cooldowns()

        triggered = engine.evaluate_node(
            "!test1", {"battery": 3, "network": "meshtastic"},
        )

        for alert in triggered:
            event = Event(
                event_type=EventType.ALERT_FIRED,
                source="alert_engine",
                data=alert.to_dict(),
            )
            bus.publish(event)

        assert len(received) >= 1
        assert received[0].event_type == EventType.ALERT_FIRED
        assert received[0].data["node_id"] == "!test1"

    def test_alert_event_contains_full_alert_data(self):
        """ALERT_FIRED event data contains the full alert dict."""
        bus = EventBus()
        received = []
        bus.subscribe(EventType.ALERT_FIRED, lambda e: received.append(e))

        engine = AlertEngine()
        engine.clear_cooldowns()
        triggered = engine.evaluate_node(
            "!test2", {"battery": 2},
        )

        for alert in triggered:
            bus.publish(Event(
                event_type=EventType.ALERT_FIRED,
                source="alert_engine",
                data=alert.to_dict(),
            ))

        assert len(received) >= 1
        data = received[0].data
        assert "alert_id" in data
        assert "severity" in data
        assert "metric" in data
        assert "value" in data
        assert "threshold" in data
        assert "message" in data

    def test_wildcard_subscriber_receives_alerts(self):
        """Wildcard subscribers (like WebSocket forwarder) get ALERT_FIRED events."""
        bus = EventBus()
        all_events = []
        bus.subscribe(None, lambda e: all_events.append(e))  # Wildcard

        event = Event(
            event_type=EventType.ALERT_FIRED,
            source="alert_engine",
            data={"alert_id": "alert-1", "severity": "critical"},
        )
        bus.publish(event)

        assert len(all_events) == 1
        assert all_events[0].event_type == EventType.ALERT_FIRED

    def test_alert_event_serializable_for_websocket(self):
        """ALERT_FIRED events can be serialized to JSON for WebSocket broadcast."""
        engine = AlertEngine()
        engine.clear_cooldowns()
        triggered = engine.evaluate_node("!ws1", {"battery": 1})

        for alert in triggered:
            event = Event(
                event_type=EventType.ALERT_FIRED,
                source="alert_engine",
                data=alert.to_dict(),
            )
            # Simulate WebSocket serialization
            msg = {
                "type": event.event_type.value,
                "timestamp": event.timestamp,
                "source": event.source,
                "data": event.data,
            }
            serialized = json.dumps(msg)
            parsed = json.loads(serialized)
            assert parsed["type"] == "alert.fired"
            assert parsed["data"]["node_id"] == "!ws1"

    def test_multiple_alerts_fire_multiple_events(self):
        """Multiple triggered alerts produce multiple ALERT_FIRED events."""
        bus = EventBus()
        received = []
        bus.subscribe(EventType.ALERT_FIRED, lambda e: received.append(e))

        engine = AlertEngine()
        engine.clear_cooldowns()

        # battery=3 should trigger battery_low (<=20) and battery_critical (<=5)
        triggered = engine.evaluate_node(
            "!multi1", {"battery": 3, "network": "meshtastic"},
        )

        for alert in triggered:
            bus.publish(Event(
                event_type=EventType.ALERT_FIRED,
                source="alert_engine",
                data=alert.to_dict(),
            ))

        assert len(received) == len(triggered)
        assert len(received) >= 2  # battery_low + battery_critical

    def test_alert_event_does_not_break_other_subscribers(self):
        """A failing subscriber doesn't prevent alert delivery to others."""
        bus = EventBus()
        received = []

        def bad_handler(e):
            raise RuntimeError("boom")

        def good_handler(e):
            received.append(e)

        bus.subscribe(EventType.ALERT_FIRED, bad_handler)
        bus.subscribe(EventType.ALERT_FIRED, good_handler)

        event = Event(
            event_type=EventType.ALERT_FIRED,
            source="alert_engine",
            data={"alert_id": "alert-1"},
        )
        bus.publish(event)

        # Good handler should still receive despite bad handler raising
        assert len(received) == 1


# ---------------------------------------------------------------------------
# Combined delivery pipeline
# ---------------------------------------------------------------------------


class TestCombinedDelivery:
    """Tests for the full delivery pipeline: callback + MQTT + webhook."""

    def test_all_delivery_channels_fire(self):
        """Alert fires callback, MQTT, and webhook delivery in sequence."""
        callback_alerts = []
        mqtt_client = MagicMock()

        engine = AlertEngine(
            on_alert=lambda a: callback_alerts.append(a),
            mqtt_client=mqtt_client,
            mqtt_topic="test/alerts",
        )
        engine.clear_cooldowns()

        # Add a webhook (won't actually connect)
        with patch("src.utils.alert_engine.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            engine.add_webhook("http://localhost:9999/hook")
            triggered = engine.evaluate_node(
                "!combo1", {"battery": 2},
            )

        assert len(triggered) >= 1
        # Callback fired
        assert len(callback_alerts) >= 1
        # MQTT published
        assert mqtt_client.publish.called
        # Webhook called
        assert mock_urlopen.called

    def test_mqtt_failure_does_not_block_webhook(self):
        """If MQTT publish fails, webhook delivery still happens."""
        mqtt_client = MagicMock()
        mqtt_client.publish.side_effect = Exception("MQTT down")

        engine = AlertEngine(mqtt_client=mqtt_client)
        engine.clear_cooldowns()

        with patch("src.utils.alert_engine.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            engine.add_webhook("http://localhost:9999/hook")
            triggered = engine.evaluate_node(
                "!failover1", {"battery": 2},
            )

        assert len(triggered) >= 1
        assert mock_urlopen.called
