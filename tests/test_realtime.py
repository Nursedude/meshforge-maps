"""Integration tests for the Phase 2 real-time architecture.

Tests the full pipeline:
    MQTT subscriber -> EventBus -> WebSocket broadcast

Also tests:
    - EventBus wiring in DataAggregator
    - WebSocket stats in /api/status
    - Event bus stats in /api/status
    - MapServer WebSocket lifecycle
"""

import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from src.utils.event_bus import EventBus, EventType, NodeEvent, ServiceEvent
from src.utils.websocket_server import HAS_WEBSOCKETS, MapWebSocketServer
from src.collectors.mqtt_subscriber import MQTTNodeStore, MQTTSubscriber

if HAS_WEBSOCKETS:
    import websockets


def _free_port():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# EventBus wiring in DataAggregator
# ---------------------------------------------------------------------------

class TestAggregatorEventBus:
    def test_aggregator_creates_event_bus(self):
        """DataAggregator should create an EventBus instance."""
        from src.collectors.aggregator import DataAggregator
        agg = DataAggregator({
            "enable_meshtastic": False,
            "enable_reticulum": False,
            "enable_hamclock": False,
            "enable_aredn": False,
        })
        assert agg.event_bus is not None
        assert isinstance(agg.event_bus, EventBus)

    def test_aggregator_passes_bus_to_mqtt(self):
        """When MQTT subscriber is created, it should receive the event bus."""
        from src.collectors.aggregator import DataAggregator
        agg = DataAggregator({
            "enable_meshtastic": True,
            "enable_reticulum": False,
            "enable_hamclock": False,
            "enable_aredn": False,
        })
        if agg._mqtt_subscriber:
            assert agg._mqtt_subscriber._event_bus is agg.event_bus
        agg.shutdown()


# ---------------------------------------------------------------------------
# MQTTSubscriber -> EventBus emission
# ---------------------------------------------------------------------------

class TestMQTTEventEmission:
    def test_notify_update_emits_position_event(self):
        """_notify_update with 'position' should publish NodeEvent.position."""
        bus = EventBus()
        received = []
        bus.subscribe(EventType.NODE_POSITION, received.append)

        sub = MQTTSubscriber(event_bus=bus)
        sub._notify_update("!abc123", "position", lat=40.0, lon=-105.0)

        assert len(received) == 1
        event = received[0]
        assert isinstance(event, NodeEvent)
        assert event.node_id == "!abc123"
        assert event.lat == 40.0
        assert event.lon == -105.0

    def test_notify_update_emits_info_event(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.NODE_INFO, received.append)

        sub = MQTTSubscriber(event_bus=bus)
        sub._notify_update("!abc123", "nodeinfo", long_name="TestNode")

        assert len(received) == 1
        assert received[0].node_id == "!abc123"
        assert received[0].data["long_name"] == "TestNode"

    def test_notify_update_emits_telemetry_event(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.NODE_TELEMETRY, received.append)

        sub = MQTTSubscriber(event_bus=bus)
        sub._notify_update("!abc123", "telemetry")

        assert len(received) == 1

    def test_notify_update_emits_topology_event(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.NODE_TOPOLOGY, received.append)

        sub = MQTTSubscriber(event_bus=bus)
        sub._notify_update("!abc123", "topology", neighbor_count=3)

        assert len(received) == 1
        assert received[0].data["neighbor_count"] == 3

    def test_callback_and_event_bus_both_fire(self):
        """Both the legacy callback and event bus should be invoked."""
        bus = EventBus()
        bus_received = []
        bus.subscribe(EventType.NODE_POSITION, bus_received.append)

        cb_received = []
        def callback(node_id, update_type):
            cb_received.append((node_id, update_type))

        sub = MQTTSubscriber(on_node_update=callback, event_bus=bus)
        sub._notify_update("!abc123", "position", lat=1.0, lon=2.0)

        assert len(cb_received) == 1
        assert cb_received[0] == ("!abc123", "position")
        assert len(bus_received) == 1

    def test_no_event_bus_does_not_error(self):
        """MQTTSubscriber without event_bus should work fine."""
        sub = MQTTSubscriber()
        sub._notify_update("!abc123", "position")  # should not raise


# ---------------------------------------------------------------------------
# Full pipeline: EventBus -> WebSocket
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_WEBSOCKETS, reason="websockets not installed")
class TestEventBusToWebSocket:
    def test_event_bus_to_websocket_broadcast(self):
        """Events published to the bus should reach WebSocket clients."""
        bus = EventBus()
        port = _free_port()
        ws_server = MapWebSocketServer(host="127.0.0.1", port=port,
                                       history_size=10)
        ws_server.start()

        # Bridge: subscribe to all events, broadcast to WS
        def forward(event):
            msg = {
                "type": event.event_type.value,
                "node_id": getattr(event, "node_id", ""),
            }
            if isinstance(event, NodeEvent):
                if event.lat is not None:
                    msg["lat"] = event.lat
                if event.lon is not None:
                    msg["lon"] = event.lon
            ws_server.broadcast(msg)

        bus.subscribe(None, forward)

        async def _test():
            async with websockets.connect(
                f"ws://127.0.0.1:{port}"
            ) as ws:
                await asyncio.sleep(0.1)

                # Publish an event on the bus
                bus.publish(NodeEvent.position("!test1", 35.0, -106.0))

                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(msg)
                assert data["type"] == "node.position"
                assert data["node_id"] == "!test1"
                assert data["lat"] == 35.0
                assert data["lon"] == -106.0

        try:
            asyncio.get_event_loop().run_until_complete(_test())
        finally:
            ws_server.shutdown()

    def test_mqtt_to_event_bus_to_websocket(self):
        """End-to-end: MQTTSubscriber -> EventBus -> WebSocket client."""
        bus = EventBus()
        port = _free_port()
        ws_server = MapWebSocketServer(host="127.0.0.1", port=port,
                                       history_size=10)
        ws_server.start()

        def forward(event):
            msg = {
                "type": event.event_type.value,
                "node_id": getattr(event, "node_id", ""),
            }
            if isinstance(event, NodeEvent) and event.lat is not None:
                msg["lat"] = event.lat
                msg["lon"] = event.lon
            ws_server.broadcast(msg)

        bus.subscribe(None, forward)

        sub = MQTTSubscriber(event_bus=bus)

        async def _test():
            async with websockets.connect(
                f"ws://127.0.0.1:{port}"
            ) as ws:
                await asyncio.sleep(0.1)

                # Simulate MQTT position update
                sub._notify_update("!mesh42", "position", lat=40.0, lon=-105.0)

                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(msg)
                assert data["type"] == "node.position"
                assert data["node_id"] == "!mesh42"
                assert data["lat"] == 40.0

        try:
            asyncio.get_event_loop().run_until_complete(_test())
        finally:
            ws_server.shutdown()


# ---------------------------------------------------------------------------
# MapServer integration
# ---------------------------------------------------------------------------

class TestMapServerRealtime:
    def _make_server(self, tmp_path):
        """Create a MapServer with all collectors disabled for fast testing."""
        from src.utils.config import MapsConfig
        from src.map_server import MapServer
        config = MapsConfig(config_path=tmp_path / "settings.json")
        config.set("enable_meshtastic", False)
        config.set("enable_reticulum", False)
        config.set("enable_hamclock", False)
        config.set("enable_aredn", False)
        config.set("http_port", _free_port())
        return MapServer(config)

    def test_map_server_creates_ws_server(self, tmp_path):
        """MapServer should have ws_server attribute after init."""
        server = self._make_server(tmp_path)
        # Before start, ws_port should be 0
        assert server.ws_port == 0

    @pytest.mark.skipif(not HAS_WEBSOCKETS, reason="websockets not installed")
    def test_map_server_starts_websocket(self, tmp_path):
        """MapServer.start() should also start the WebSocket server."""
        server = self._make_server(tmp_path)
        try:
            started = server.start()
            assert started
            assert server.ws_port > 0
            assert server.ws_port == server.port + 1
        finally:
            server.stop()

    @pytest.mark.skipif(not HAS_WEBSOCKETS, reason="websockets not installed")
    def test_status_includes_websocket_stats(self, tmp_path):
        """GET /api/status should include websocket stats."""
        import urllib.request
        server = self._make_server(tmp_path)
        try:
            server.start()
            url = f"http://127.0.0.1:{server.port}/api/status"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
                assert "websocket" in data
                assert data["websocket"]["clients_connected"] == 0
        finally:
            server.stop()

    @pytest.mark.skipif(not HAS_WEBSOCKETS, reason="websockets not installed")
    def test_status_includes_event_bus_stats(self, tmp_path):
        """GET /api/status should include event bus stats."""
        import urllib.request
        server = self._make_server(tmp_path)
        try:
            server.start()
            url = f"http://127.0.0.1:{server.port}/api/status"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
                assert "event_bus" in data
                assert data["event_bus"]["total_published"] == 0
        finally:
            server.stop()

    @pytest.mark.skipif(not HAS_WEBSOCKETS, reason="websockets not installed")
    def test_config_includes_ws_port(self, tmp_path):
        """GET /api/config should include ws_port."""
        import urllib.request
        server = self._make_server(tmp_path)
        try:
            server.start()
            url = f"http://127.0.0.1:{server.port}/api/config"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
                assert "ws_port" in data
                assert data["ws_port"] == server.ws_port
        finally:
            server.stop()

    @pytest.mark.skipif(not HAS_WEBSOCKETS, reason="websockets not installed")
    def test_event_reaches_websocket_via_server(self, tmp_path):
        """Publishing on the aggregator's event bus should reach WS clients."""
        server = self._make_server(tmp_path)
        try:
            server.start()

            async def _test():
                async with websockets.connect(
                    f"ws://127.0.0.1:{server.ws_port}"
                ) as ws:
                    await asyncio.sleep(0.1)

                    # Publish event via aggregator's bus
                    server.aggregator.event_bus.publish(
                        NodeEvent.position("!live1", 42.0, -71.0)
                    )

                    msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    data = json.loads(msg)
                    assert data["type"] == "node.position"
                    assert data["node_id"] == "!live1"

            asyncio.get_event_loop().run_until_complete(_test())
        finally:
            server.stop()
