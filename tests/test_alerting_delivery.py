"""Tests for alerting delivery: EventBus ALERT_FIRED propagation."""

import json
import time
from unittest.mock import MagicMock

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
