"""Tests for the WebSocket broadcast server."""

import asyncio
import json
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

from src.utils.websocket_server import MapWebSocketServer, HAS_WEBSOCKETS

# Skip entire module if websockets not installed
pytestmark = pytest.mark.skipif(
    not HAS_WEBSOCKETS, reason="websockets library not installed"
)

if HAS_WEBSOCKETS:
    import websockets


def _free_port():
    """Get an available port for testing."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def ws_server():
    """Start a WebSocket server on a random port, shut down after test."""
    port = _free_port()
    server = MapWebSocketServer(host="127.0.0.1", port=port, history_size=10)
    started = server.start()
    assert started, "WebSocket server failed to start"
    yield server
    server.shutdown()


async def _connect_and_receive(port, timeout=2.0, count=1):
    """Helper: connect to server, receive `count` messages, return them."""
    messages = []
    async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
        for _ in range(count):
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            messages.append(json.loads(msg))
    return messages


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

class TestServerLifecycle:
    def test_start_and_stop(self):
        port = _free_port()
        server = MapWebSocketServer(host="127.0.0.1", port=port)
        assert server.start()
        assert server.client_count == 0
        server.shutdown()

    def test_double_start_returns_false(self, ws_server):
        assert ws_server.start() is False

    def test_stats_initial(self, ws_server):
        stats = ws_server.stats
        assert stats["clients_connected"] == 0
        assert stats["total_connections"] == 0
        assert stats["total_messages_sent"] == 0
        assert stats["history_size"] == 0


# ---------------------------------------------------------------------------
# Client connection
# ---------------------------------------------------------------------------

class TestClientConnection:
    def test_client_connects(self, ws_server):
        async def _test():
            async with websockets.connect(
                f"ws://127.0.0.1:{ws_server.port}"
            ):
                # Brief pause for server to register
                await asyncio.sleep(0.1)
                assert ws_server.client_count == 1

        asyncio.get_event_loop().run_until_complete(_test())

    def test_client_disconnect_decrements_count(self, ws_server):
        async def _test():
            ws = await websockets.connect(
                f"ws://127.0.0.1:{ws_server.port}"
            )
            await asyncio.sleep(0.1)
            assert ws_server.client_count == 1
            await ws.close()
            await asyncio.sleep(0.2)
            assert ws_server.client_count == 0

        asyncio.get_event_loop().run_until_complete(_test())

    def test_multiple_clients(self, ws_server):
        async def _test():
            clients = []
            for _ in range(3):
                ws = await websockets.connect(
                    f"ws://127.0.0.1:{ws_server.port}"
                )
                clients.append(ws)
            await asyncio.sleep(0.1)
            assert ws_server.client_count == 3
            for ws in clients:
                await ws.close()
            await asyncio.sleep(0.2)
            assert ws_server.client_count == 0

        asyncio.get_event_loop().run_until_complete(_test())


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------

class TestBroadcast:
    def test_broadcast_to_single_client(self, ws_server):
        async def _test():
            async with websockets.connect(
                f"ws://127.0.0.1:{ws_server.port}"
            ) as ws:
                await asyncio.sleep(0.1)
                ws_server.broadcast({"type": "test", "value": 42})
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(msg)
                assert data["type"] == "test"
                assert data["value"] == 42

        asyncio.get_event_loop().run_until_complete(_test())

    def test_broadcast_to_multiple_clients(self, ws_server):
        async def _test():
            clients = []
            for _ in range(3):
                ws = await websockets.connect(
                    f"ws://127.0.0.1:{ws_server.port}"
                )
                clients.append(ws)
            await asyncio.sleep(0.1)

            ws_server.broadcast({"type": "multi", "n": 3})

            for ws in clients:
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(msg)
                assert data["type"] == "multi"

            for ws in clients:
                await ws.close()

        asyncio.get_event_loop().run_until_complete(_test())

    def test_broadcast_no_clients_does_not_error(self, ws_server):
        ws_server.broadcast({"type": "void"})  # should not raise

    def test_broadcast_when_not_running(self):
        server = MapWebSocketServer(host="127.0.0.1", port=_free_port())
        server.broadcast({"type": "noop"})  # should not raise


# ---------------------------------------------------------------------------
# History buffer
# ---------------------------------------------------------------------------

class TestHistory:
    def test_new_client_receives_history(self, ws_server):
        # Pre-fill history
        for i in range(3):
            ws_server.broadcast({"type": "hist", "seq": i})
        time.sleep(0.2)  # let broadcasts process

        async def _test():
            async with websockets.connect(
                f"ws://127.0.0.1:{ws_server.port}"
            ) as ws:
                messages = []
                for _ in range(3):
                    msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    messages.append(json.loads(msg))
                assert [m["seq"] for m in messages] == [0, 1, 2]

        asyncio.get_event_loop().run_until_complete(_test())

    def test_history_capped_at_max_size(self, ws_server):
        # ws_server has history_size=10
        for i in range(20):
            ws_server.broadcast({"type": "fill", "seq": i})
        time.sleep(0.3)

        async def _test():
            async with websockets.connect(
                f"ws://127.0.0.1:{ws_server.port}"
            ) as ws:
                messages = []
                for _ in range(10):
                    msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    messages.append(json.loads(msg))
                # Should get the last 10 (seq 10-19)
                seqs = [m["seq"] for m in messages]
                assert seqs == list(range(10, 20))

        asyncio.get_event_loop().run_until_complete(_test())


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestWSStats:
    def test_connection_counted(self, ws_server):
        async def _test():
            ws = await websockets.connect(
                f"ws://127.0.0.1:{ws_server.port}"
            )
            await asyncio.sleep(0.1)
            await ws.close()
            await asyncio.sleep(0.1)

        asyncio.get_event_loop().run_until_complete(_test())
        assert ws_server.stats["total_connections"] == 1

    def test_messages_counted(self, ws_server):
        async def _test():
            async with websockets.connect(
                f"ws://127.0.0.1:{ws_server.port}"
            ) as ws:
                await asyncio.sleep(0.1)
                ws_server.broadcast({"n": 1})
                ws_server.broadcast({"n": 2})
                await asyncio.wait_for(ws.recv(), timeout=2.0)
                await asyncio.wait_for(ws.recv(), timeout=2.0)

        asyncio.get_event_loop().run_until_complete(_test())
        assert ws_server.stats["total_messages_sent"] >= 2


# ---------------------------------------------------------------------------
# Optional dependency handling
# ---------------------------------------------------------------------------

class TestClientMessageHandling:
    """Test WebSocket client message type validation."""

    def test_ping_returns_pong(self, ws_server):
        """Sending a ping message gets a pong response."""
        async def _test():
            async with websockets.connect(
                f"ws://127.0.0.1:{ws_server.port}"
            ) as ws:
                await asyncio.sleep(0.1)
                await ws.send(json.dumps({"type": "ping"}))
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(msg)
                assert data["type"] == "pong"
                assert "timestamp" in data

        asyncio.get_event_loop().run_until_complete(_test())

    def test_get_stats_returns_stats(self, ws_server):
        """Sending get_stats returns server statistics."""
        async def _test():
            async with websockets.connect(
                f"ws://127.0.0.1:{ws_server.port}"
            ) as ws:
                await asyncio.sleep(0.1)
                await ws.send(json.dumps({"type": "get_stats"}))
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(msg)
                assert data["type"] == "stats"
                assert "data" in data

        asyncio.get_event_loop().run_until_complete(_test())

    def test_unknown_type_silently_dropped(self, ws_server):
        """Messages with unrecognized types are silently dropped."""
        async def _test():
            async with websockets.connect(
                f"ws://127.0.0.1:{ws_server.port}"
            ) as ws:
                await asyncio.sleep(0.1)
                await ws.send(json.dumps({"type": "evil_command"}))
                # Broadcast something real and verify we only get that
                ws_server.broadcast({"type": "marker", "seq": 1})
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(msg)
                assert data["type"] == "marker"

        asyncio.get_event_loop().run_until_complete(_test())

    def test_malformed_json_silently_dropped(self, ws_server):
        """Malformed JSON from client is silently dropped."""
        async def _test():
            async with websockets.connect(
                f"ws://127.0.0.1:{ws_server.port}"
            ) as ws:
                await asyncio.sleep(0.1)
                await ws.send("not json at all")
                # Should still be connected — send a ping to verify
                await ws.send(json.dumps({"type": "ping"}))
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(msg)
                assert data["type"] == "pong"

        asyncio.get_event_loop().run_until_complete(_test())


class TestOriginValidation:
    """Test WebSocket origin restriction."""

    def test_allowed_origins_attribute(self):
        """Server has allowed origins configured."""
        server = MapWebSocketServer(host="127.0.0.1", port=_free_port())
        assert "http://localhost" in server._ALLOWED_ORIGINS
        assert "https://localhost" in server._ALLOWED_ORIGINS

    def test_allowed_msg_types_attribute(self):
        """Server has a finite set of allowed message types."""
        server = MapWebSocketServer(host="127.0.0.1", port=_free_port())
        assert "ping" in server._ALLOWED_MSG_TYPES
        assert "get_history" in server._ALLOWED_MSG_TYPES
        assert "get_stats" in server._ALLOWED_MSG_TYPES
        # Arbitrary types should NOT be allowed
        assert "exec" not in server._ALLOWED_MSG_TYPES


class TestOptionalDependency:
    def test_start_without_websockets_returns_false(self):
        """When websockets is not installed, start() returns False."""
        with patch("src.utils.websocket_server.HAS_WEBSOCKETS", False):
            server = MapWebSocketServer(host="127.0.0.1", port=_free_port())
            assert server.start() is False
