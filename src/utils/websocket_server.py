"""WebSocket broadcast server for real-time map updates.

Runs in a background thread with its own asyncio event loop. Connected
clients receive JSON messages whenever nodes are updated. Includes a
recent-message history buffer so newly-connected clients get caught up.

The server is optional -- if the ``websockets`` library is not installed,
``MapWebSocketServer.start()`` logs a warning and returns gracefully.

Usage:
    ws = MapWebSocketServer(port=8809)
    ws.start()                          # non-blocking, spawns thread
    ws.broadcast({"type": "node.position", "node_id": "!abc"})
    ws.shutdown()
"""

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import asyncio
    import websockets
    import websockets.asyncio.server
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False


class MapWebSocketServer:
    """Async WebSocket broadcast server running in a background thread.

    Args:
        host: Bind address (default "127.0.0.1").
        port: WebSocket port (default 8809).
        history_size: Number of recent messages to replay to new clients.
    """

    # Allowed WebSocket origin prefixes (localhost only by default)
    _ALLOWED_ORIGINS = ["http://localhost", "https://localhost"]

    # Allowed client message types
    _ALLOWED_MSG_TYPES = frozenset({"ping", "get_history", "get_stats"})

    def __init__(self, host: str = "127.0.0.1", port: int = 8809,
                 history_size: int = 50) -> None:
        self.host = host
        self.port = port
        self.history_size = history_size

        self._clients: set = set()
        self._lock = threading.Lock()
        self._history: deque = deque(maxlen=history_size)
        self._loop: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[Any] = None
        self._started = threading.Event()
        self._stats = _WSStats()

    # ------------------------------------------------------------------
    # Public API (called from any thread)
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Start the WebSocket server in a background thread.

        Returns True if started successfully, False if websockets is
        unavailable or the server is already running.
        """
        if not HAS_WEBSOCKETS:
            logger.warning(
                "websockets library not installed -- "
                "real-time updates disabled (pip install websockets)"
            )
            return False

        if self._thread and self._thread.is_alive():
            logger.debug("WebSocket server already running")
            return False

        self._started.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="websocket-server",
            daemon=True,
        )
        self._thread.start()
        # Wait for the server to actually bind (up to 5s)
        self._started.wait(timeout=5.0)
        return self._started.is_set()

    def shutdown(self) -> None:
        """Stop the server and close all connections."""
        loop = self._loop
        server = self._server
        if loop and server:
            try:
                if loop.is_running():
                    # Close the WebSocket server first, then stop the loop
                    loop.call_soon_threadsafe(self._close_server_and_stop, server, loop)
            except RuntimeError:
                pass  # Loop already closed
        elif loop:
            try:
                if loop.is_running():
                    loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if self._thread:
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                logger.warning("WebSocket server thread did not exit within 3s")
            self._thread = None
        self._loop = None
        self._server = None
        with self._lock:
            self._clients.clear()

    @staticmethod
    def _close_server_and_stop(server, loop) -> None:
        """Close the WebSocket server and stop the event loop."""
        try:
            server.close()
        except Exception:
            pass
        loop.stop()

    def broadcast(self, message: Dict[str, Any]) -> None:
        """Send a JSON message to all connected clients.

        Thread-safe. Can be called from any thread (MQTT callback, etc.).
        Messages are also added to the history buffer for new clients.
        The lock covers both history append and broadcast scheduling to
        prevent a new client from receiving a duplicate message if it
        connects between the two operations.
        """
        if not self._loop or not self._loop.is_running():
            return

        text = json.dumps(message)

        with self._lock:
            self._history.append(text)
            # Schedule broadcast while still holding the lock so a newly
            # connecting client sees either history OR broadcast, not both
            try:
                self._loop.call_soon_threadsafe(
                    self._loop.create_task,
                    self._broadcast_async(text),
                )
            except RuntimeError:
                # Event loop closed between the check and the call
                pass

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "clients_connected": self.client_count,
            "total_connections": self._stats.total_connections,
            "total_messages_sent": self._stats.total_messages_sent,
            "history_size": len(self._history),
        }

    # ------------------------------------------------------------------
    # Async internals (run on the event loop thread)
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Entry point for the background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception:
            logger.exception("WebSocket server loop error")
        finally:
            self._loop.close()

    async def _serve(self) -> None:
        """Start serving and signal readiness."""
        try:
            serve_kwargs: Dict[str, Any] = {}
            # Restrict allowed origins to localhost (prevents cross-site
            # WebSocket hijacking). Older websockets versions may not
            # support the origins parameter.
            try:
                serve_kwargs["origins"] = self._ALLOWED_ORIGINS
            except Exception:
                pass
            self._server = await websockets.asyncio.server.serve(
                self._handler,
                self.host,
                self.port,
                **serve_kwargs,
            )
            logger.info("WebSocket server listening on ws://%s:%d",
                        self.host, self.port)
            self._started.set()
            # Run until the loop is stopped
            await asyncio.get_event_loop().create_future()
        except OSError as e:
            logger.error("WebSocket server failed to bind: %s", e)
            self._started.set()  # unblock the waiter even on failure

    async def _handler(self, websocket) -> None:
        """Handle a single client connection."""
        with self._lock:
            self._clients.add(websocket)
        self._stats.record_connection()
        client_addr = f"{websocket.remote_address}" if hasattr(websocket, "remote_address") else "unknown"
        logger.info("WebSocket client connected: %s (total: %d)",
                     client_addr, self.client_count)

        try:
            # Send history buffer to new client
            with self._lock:
                history = list(self._history)
            for msg in history:
                await websocket.send(msg)
                self._stats.record_message_sent()

            # Keep connection alive and handle client messages.
            # Only accept messages with a recognized type.
            async for raw in websocket:
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        await self._handle_client_message(websocket, data)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.debug("WebSocket: dropped malformed client message: %s", e)
        except Exception as e:
            logger.debug("WebSocket client connection error: %s", e)
        finally:
            with self._lock:
                self._clients.discard(websocket)
            logger.info("WebSocket client disconnected: %s (total: %d)",
                         client_addr, self.client_count)

    async def _handle_client_message(self, websocket, data: Dict[str, Any]) -> None:
        """Handle a validated client message."""
        msg_type = data.get("type")
        if msg_type not in self._ALLOWED_MSG_TYPES:
            return

        if msg_type == "ping":
            await websocket.send(json.dumps({
                "type": "pong",
                "timestamp": time.time(),
            }))
        elif msg_type == "get_history":
            limit = min(int(data.get("limit", 50)), self.history_size)
            with self._lock:
                history = list(self._history)
            await websocket.send(json.dumps({
                "type": "history",
                "messages": history[-limit:],
            }))
        elif msg_type == "get_stats":
            await websocket.send(json.dumps({
                "type": "stats",
                "data": self.stats,
            }))

    async def _broadcast_async(self, text: str) -> None:
        """Send a text message to all connected clients."""
        with self._lock:
            clients = list(self._clients)

        if not clients:
            return

        for client in clients:
            try:
                await client.send(text)
                self._stats.record_message_sent()
            except Exception:
                # Client will be cleaned up in _handler
                pass


class _WSStats:
    """Thread-safe counters for WebSocket diagnostics."""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total_connections = 0
        self._total_messages_sent = 0

    @property
    def total_connections(self) -> int:
        with self._lock:
            return self._total_connections

    @property
    def total_messages_sent(self) -> int:
        with self._lock:
            return self._total_messages_sent

    def record_connection(self) -> None:
        with self._lock:
            self._total_connections += 1

    def record_message_sent(self, count: int = 1) -> None:
        with self._lock:
            self._total_messages_sent += count
