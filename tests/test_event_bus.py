"""Tests for the event bus pub/sub system."""

import threading
import time
from unittest.mock import MagicMock

import pytest

from src.utils.event_bus import (
    Event,
    EventBus,
    EventType,
    NodeEvent,
    ServiceEvent,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bus():
    return EventBus()


# ---------------------------------------------------------------------------
# Event construction
# ---------------------------------------------------------------------------

class TestNodeEvent:
    def test_position_factory(self):
        e = NodeEvent.position("!abc123", 40.0, -105.0, altitude=1500)
        assert e.event_type == EventType.NODE_POSITION
        assert e.node_id == "!abc123"
        assert e.lat == 40.0
        assert e.lon == -105.0
        assert e.source == "mqtt"
        assert e.data["altitude"] == 1500

    def test_info_factory(self):
        e = NodeEvent.info("!abc123", long_name="TestNode")
        assert e.event_type == EventType.NODE_INFO
        assert e.node_id == "!abc123"
        assert e.data["long_name"] == "TestNode"

    def test_telemetry_factory(self):
        e = NodeEvent.telemetry("!abc123", battery=85)
        assert e.event_type == EventType.NODE_TELEMETRY
        assert e.data["battery"] == 85

    def test_topology_factory(self):
        e = NodeEvent.topology("!abc123", neighbors=3)
        assert e.event_type == EventType.NODE_TOPOLOGY
        assert e.data["neighbors"] == 3

    def test_timestamp_auto_set(self):
        before = time.time()
        e = NodeEvent.position("!a", 0.0, 0.0)
        after = time.time()
        assert before <= e.timestamp <= after


class TestServiceEvent:
    def test_up_factory(self):
        e = ServiceEvent.up("meshtastic")
        assert e.event_type == EventType.SERVICE_UP
        assert e.service_name == "meshtastic"
        assert e.source == "meshtastic"

    def test_down_factory(self):
        e = ServiceEvent.down("hamclock", reason="timeout")
        assert e.event_type == EventType.SERVICE_DOWN
        assert e.data["reason"] == "timeout"

    def test_degraded_factory(self):
        e = ServiceEvent.degraded("aredn", reason="slow")
        assert e.event_type == EventType.SERVICE_DEGRADED
        assert e.data["reason"] == "slow"


# ---------------------------------------------------------------------------
# Subscribe / publish
# ---------------------------------------------------------------------------

class TestSubscribePublish:
    def test_basic_delivery(self, bus):
        received = []
        bus.subscribe(EventType.NODE_POSITION, received.append)
        event = NodeEvent.position("!a", 1.0, 2.0)
        bus.publish(event)
        assert len(received) == 1
        assert received[0] is event

    def test_multiple_subscribers(self, bus):
        cb1 = MagicMock()
        cb2 = MagicMock()
        bus.subscribe(EventType.NODE_POSITION, cb1)
        bus.subscribe(EventType.NODE_POSITION, cb2)
        bus.publish(NodeEvent.position("!a", 1.0, 2.0))
        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_type_filtering(self, bus):
        pos_cb = MagicMock()
        info_cb = MagicMock()
        bus.subscribe(EventType.NODE_POSITION, pos_cb)
        bus.subscribe(EventType.NODE_INFO, info_cb)

        bus.publish(NodeEvent.position("!a", 1.0, 2.0))

        pos_cb.assert_called_once()
        info_cb.assert_not_called()

    def test_wildcard_receives_all(self, bus):
        received = []
        bus.subscribe(None, received.append)

        bus.publish(NodeEvent.position("!a", 1.0, 2.0))
        bus.publish(ServiceEvent.up("mqtt"))
        bus.publish(NodeEvent.info("!b"))

        assert len(received) == 3

    def test_wildcard_plus_specific(self, bus):
        wild = MagicMock()
        specific = MagicMock()
        bus.subscribe(None, wild)
        bus.subscribe(EventType.NODE_POSITION, specific)

        bus.publish(NodeEvent.position("!a", 1.0, 2.0))

        wild.assert_called_once()
        specific.assert_called_once()

    def test_no_subscribers_no_error(self, bus):
        bus.publish(NodeEvent.position("!a", 1.0, 2.0))  # should not raise


# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------

class TestUnsubscribe:
    def test_unsubscribe_stops_delivery(self, bus):
        cb = MagicMock()
        bus.subscribe(EventType.NODE_POSITION, cb)
        bus.unsubscribe(EventType.NODE_POSITION, cb)
        bus.publish(NodeEvent.position("!a", 1.0, 2.0))
        cb.assert_not_called()

    def test_unsubscribe_nonexistent_callback(self, bus):
        # Should not raise
        bus.unsubscribe(EventType.NODE_POSITION, lambda e: None)

    def test_unsubscribe_nonexistent_type(self, bus):
        bus.unsubscribe(EventType.NODE_POSITION, lambda e: None)

    def test_unsubscribe_wildcard(self, bus):
        cb = MagicMock()
        bus.subscribe(None, cb)
        bus.unsubscribe(None, cb)
        bus.publish(NodeEvent.position("!a", 1.0, 2.0))
        cb.assert_not_called()


# ---------------------------------------------------------------------------
# Error isolation
# ---------------------------------------------------------------------------

class TestErrorIsolation:
    def test_bad_subscriber_does_not_break_others(self, bus):
        good = MagicMock()

        def bad(event):
            raise ValueError("boom")

        bus.subscribe(EventType.NODE_POSITION, bad)
        bus.subscribe(EventType.NODE_POSITION, good)

        bus.publish(NodeEvent.position("!a", 1.0, 2.0))

        # Good subscriber still called despite bad one raising
        good.assert_called_once()

    def test_error_increments_stats(self, bus):
        def bad(event):
            raise RuntimeError("fail")

        bus.subscribe(EventType.NODE_POSITION, bad)
        bus.publish(NodeEvent.position("!a", 1.0, 2.0))

        assert bus.stats["total_errors"] == 1


# ---------------------------------------------------------------------------
# Stats and counts
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_after_publish(self, bus):
        cb = MagicMock()
        bus.subscribe(EventType.NODE_POSITION, cb)

        bus.publish(NodeEvent.position("!a", 1.0, 2.0))
        bus.publish(NodeEvent.position("!b", 3.0, 4.0))

        assert bus.stats["total_published"] == 2
        assert bus.stats["total_delivered"] == 2
        assert bus.stats["total_errors"] == 0

    def test_subscriber_count_specific(self, bus):
        bus.subscribe(EventType.NODE_POSITION, lambda e: None)
        bus.subscribe(EventType.NODE_POSITION, lambda e: None)
        bus.subscribe(EventType.NODE_INFO, lambda e: None)

        assert bus.subscriber_count(EventType.NODE_POSITION) == 2
        assert bus.subscriber_count(EventType.NODE_INFO) == 1
        assert bus.subscriber_count(EventType.SERVICE_UP) == 0

    def test_subscriber_count_total(self, bus):
        bus.subscribe(EventType.NODE_POSITION, lambda e: None)
        bus.subscribe(EventType.SERVICE_UP, lambda e: None)
        bus.subscribe(None, lambda e: None)

        assert bus.subscriber_count() == 3

    def test_reset_clears_everything(self, bus):
        cb = MagicMock()
        bus.subscribe(EventType.NODE_POSITION, cb)
        bus.publish(NodeEvent.position("!a", 1.0, 2.0))

        bus.reset()

        assert bus.subscriber_count() == 0
        assert bus.stats["total_published"] == 0
        bus.publish(NodeEvent.position("!b", 1.0, 2.0))
        cb.assert_called_once()  # only the first call, not after reset


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_publish_subscribe(self, bus):
        """Multiple threads publishing and subscribing concurrently."""
        received = []
        lock = threading.Lock()

        def safe_append(event):
            with lock:
                received.append(event)

        bus.subscribe(EventType.NODE_POSITION, safe_append)

        def publisher(n):
            for i in range(50):
                bus.publish(NodeEvent.position(f"!{n}_{i}", float(i), 0.0))

        threads = [threading.Thread(target=publisher, args=(t,))
                   for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(received) == 200  # 4 threads * 50 events

    def test_subscribe_during_publish(self, bus):
        """Adding subscribers while publishing doesn't deadlock."""
        results = []

        def subscriber_that_subscribes(event):
            results.append(event)
            # Subscribe a new handler during publish
            bus.subscribe(EventType.NODE_INFO, lambda e: results.append(e))

        bus.subscribe(EventType.NODE_POSITION, subscriber_that_subscribes)
        bus.publish(NodeEvent.position("!a", 1.0, 2.0))

        assert len(results) == 1  # original event received
        # New subscriber should work on next publish
        bus.publish(NodeEvent.info("!b"))
        assert len(results) == 2
