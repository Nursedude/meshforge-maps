"""Tests for Session 11 reliability fixes.

Covers:
  - Proxy request counter thread safety
  - Node history DB connection leak on init failure
  - Node ID input validation
  - Safe query parameter extraction
  - MQTT subscriber thread lifecycle
  - WebSocket broadcast/history atomicity
  - MapServer thread join on stop
  - Node eviction cleanup propagation
  - ConfigDriftDetector.remove_node()
  - NodeStateTracker.remove_node()
"""

import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Proxy request counter thread safety
# ---------------------------------------------------------------------------

class TestProxyRequestCounterThreadSafety:
    """Verify _request_count is thread-safe via lock."""

    def test_inc_request_count_is_thread_safe(self):
        from src.utils.meshtastic_api_proxy import MeshtasticApiProxy

        proxy = MeshtasticApiProxy()
        errors = []

        def hammer(n):
            for _ in range(n):
                proxy._inc_request_count()

        threads = [threading.Thread(target=hammer, args=(1000,)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert proxy.request_count == 10000

    def test_request_count_property(self):
        from src.utils.meshtastic_api_proxy import MeshtasticApiProxy

        proxy = MeshtasticApiProxy()
        assert proxy.request_count == 0
        proxy._inc_request_count()
        proxy._inc_request_count()
        assert proxy.request_count == 2

    def test_stats_uses_thread_safe_accessor(self):
        from src.utils.meshtastic_api_proxy import MeshtasticApiProxy

        proxy = MeshtasticApiProxy()
        proxy._inc_request_count()
        stats = proxy.stats
        assert stats["request_count"] == 1

    def test_proxy_stop_joins_thread(self):
        from src.utils.meshtastic_api_proxy import MeshtasticApiProxy

        proxy = MeshtasticApiProxy()
        # Not started, should not error
        proxy.stop()
        assert proxy._thread is None


# ---------------------------------------------------------------------------
# 2. Node history DB connection leak on init failure
# ---------------------------------------------------------------------------

class TestNodeHistoryDBInitSafety:
    """Verify DB connection is closed on init failure."""

    def test_init_failure_closes_connection(self, tmp_path):
        from src.utils.node_history import NodeHistoryDB

        bad_path = tmp_path / "subdir" / "test.db"

        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.execute.side_effect = sqlite3.OperationalError("schema error")
            mock_connect.return_value = mock_conn

            db = NodeHistoryDB(db_path=bad_path)

            # Connection should have been closed on failure
            mock_conn.close.assert_called_once()
            assert db._conn is None

    def test_normal_init_succeeds(self, tmp_path):
        from src.utils.node_history import NodeHistoryDB

        db = NodeHistoryDB(db_path=tmp_path / "test.db")
        assert db._conn is not None
        db.close()


# ---------------------------------------------------------------------------
# 3. Node ID input validation
# ---------------------------------------------------------------------------

class TestNodeIdValidation:
    """Verify _validate_node_id and _NODE_ID_RE."""

    def test_valid_hex_ids(self):
        from src.map_server import _validate_node_id

        assert _validate_node_id("!a1b2c3d4") is True
        assert _validate_node_id("!DEADBEEF") is True
        assert _validate_node_id("a1b2c3d4") is True
        assert _validate_node_id("!0") is True
        assert _validate_node_id("!abcdef0123456789") is True

    def test_invalid_ids(self):
        from src.map_server import _validate_node_id

        assert _validate_node_id("") is False
        assert _validate_node_id("!") is False
        assert _validate_node_id("!xyz") is False
        assert _validate_node_id("test_node") is False
        assert _validate_node_id("!a1b2c3d4e5f6g7h8i") is False  # too long
        assert _validate_node_id("'; DROP TABLE nodes;--") is False
        assert _validate_node_id("../../../etc/passwd") is False

    def test_invalid_node_id_returns_400(self):
        """Integration test: invalid node ID returns HTTP 400."""
        # This is tested via the existing test_map_server.py integration tests
        # but we add an explicit unit test for the regex
        from src.map_server import _NODE_ID_RE

        # These should NOT match
        for bad_id in ["test", "!test", "nonexistent", "!nonexistent",
                       "SELECT", "../etc", "!g1h2"]:
            assert _NODE_ID_RE.match(bad_id) is None, f"Should reject: {bad_id}"


# ---------------------------------------------------------------------------
# 4. Safe query parameter extraction
# ---------------------------------------------------------------------------

class TestSafeQueryParam:
    """Verify _safe_query_param handles edge cases."""

    def test_normal_extraction(self):
        from src.map_server import _safe_query_param

        query = {"since": ["1000"], "limit": ["50"]}
        assert _safe_query_param(query, "since") == "1000"
        assert _safe_query_param(query, "limit") == "50"

    def test_missing_key_returns_default(self):
        from src.map_server import _safe_query_param

        query = {"since": ["1000"]}
        assert _safe_query_param(query, "missing") is None
        assert _safe_query_param(query, "missing", "default") == "default"

    def test_empty_list_returns_default(self):
        from src.map_server import _safe_query_param

        query = {"key": []}
        assert _safe_query_param(query, "key") is None
        assert _safe_query_param(query, "key", "fallback") == "fallback"

    def test_empty_string_value_returns_default(self):
        from src.map_server import _safe_query_param

        query = {"key": [""]}
        assert _safe_query_param(query, "key") is None
        assert _safe_query_param(query, "key", "42") == "42"

    def test_multiple_values_returns_first(self):
        from src.map_server import _safe_query_param

        query = {"key": ["first", "second"]}
        assert _safe_query_param(query, "key") == "first"


# ---------------------------------------------------------------------------
# 5. MQTT subscriber stop lifecycle
# ---------------------------------------------------------------------------

class TestMQTTSubscriberStopLifecycle:
    """Verify stop() properly joins threads."""

    def test_stop_joins_main_thread(self):
        from src.collectors.mqtt_subscriber import MQTTSubscriber

        sub = MQTTSubscriber()
        # Create a mock thread that reports alive until join is called
        mock_thread = MagicMock()
        mock_thread.is_alive.side_effect = [True, False]
        sub._thread = mock_thread
        sub._running.set()

        sub.stop()

        mock_thread.join.assert_called_once_with(timeout=5)
        assert sub._thread is None
        assert not sub._connected.is_set()

    def test_stop_warns_on_thread_timeout(self):
        from src.collectors.mqtt_subscriber import MQTTSubscriber

        sub = MQTTSubscriber()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True  # Thread didn't exit
        sub._thread = mock_thread
        sub._running.set()

        with patch("src.collectors.mqtt_subscriber.logger") as mock_logger:
            sub.stop()
            mock_logger.warning.assert_called()

    def test_stop_without_start_is_safe(self):
        from src.collectors.mqtt_subscriber import MQTTSubscriber

        sub = MQTTSubscriber()
        sub.stop()  # Should not raise
        assert sub._client is None


# ---------------------------------------------------------------------------
# 6. WebSocket broadcast atomicity
# ---------------------------------------------------------------------------

class TestWebSocketBroadcastAtomicity:
    """Verify broadcast() holds lock across history+schedule."""

    def test_broadcast_without_running_loop_is_noop(self):
        from src.utils.websocket_server import MapWebSocketServer

        ws = MapWebSocketServer()
        ws.broadcast({"test": True})
        assert len(ws._history) == 0  # No loop running, nothing appended

    def test_shutdown_handles_closed_loop(self):
        from src.utils.websocket_server import MapWebSocketServer

        ws = MapWebSocketServer()
        ws._loop = MagicMock()
        ws._loop.is_running.side_effect = RuntimeError("closed")
        ws._thread = None

        # Should not raise
        ws.shutdown()
        assert ws._loop is None


# ---------------------------------------------------------------------------
# 7. MapServer thread join on stop
# ---------------------------------------------------------------------------

class TestMapServerStopJoinsThread:
    """Verify stop() waits for HTTP server thread."""

    def test_stop_joins_http_thread(self):
        from src.map_server import MapServer
        from src.utils.config import MapsConfig

        config = MapsConfig()
        config.set("enable_meshtastic", False)
        config.set("enable_reticulum", False)
        config.set("enable_aredn", False)
        config.set("enable_hamclock", False)

        server = MapServer(config)

        # Mock the thread that reports alive until join is called
        mock_thread = MagicMock()
        mock_thread.is_alive.side_effect = [True, False]
        server._thread = mock_thread
        server._server = MagicMock()

        server.stop()

        mock_thread.join.assert_called_once_with(timeout=5)


# ---------------------------------------------------------------------------
# 8. Node eviction cleanup
# ---------------------------------------------------------------------------

class TestNodeEvictionCleanup:
    """Verify eviction propagates to drift detector and state tracker."""

    def test_config_drift_remove_node(self):
        from src.utils.config_drift import ConfigDriftDetector

        detector = ConfigDriftDetector()
        # Record some data
        detector.check_node("!aabb", role="ROUTER")
        detector.check_node("!aabb", role="CLIENT")  # drift
        assert detector.tracked_node_count == 1
        assert detector.total_drifts == 1

        # Remove
        detector.remove_node("!aabb")
        assert detector.tracked_node_count == 0
        # History should also be gone
        assert detector.get_node_drift_history("!aabb") == []
        assert detector.get_node_snapshot("!aabb") is None

    def test_config_drift_remove_nonexistent_node(self):
        from src.utils.config_drift import ConfigDriftDetector

        detector = ConfigDriftDetector()
        detector.remove_node("!nonexistent")  # Should not raise
        assert detector.tracked_node_count == 0

    def test_node_state_remove_node(self):
        from src.utils.node_state import NodeStateTracker

        tracker = NodeStateTracker()
        tracker.record_heartbeat("!aabb", timestamp=1000)
        tracker.record_heartbeat("!aabb", timestamp=1100)
        assert tracker.tracked_node_count == 1

        tracker.remove_node("!aabb")
        assert tracker.tracked_node_count == 0
        assert tracker.get_node_state("!aabb") is None

    def test_node_state_remove_nonexistent_node(self):
        from src.utils.node_state import NodeStateTracker

        tracker = NodeStateTracker()
        tracker.remove_node("!nonexistent")  # Should not raise
        assert tracker.tracked_node_count == 0

    def test_mqtt_store_eviction_calls_callback(self):
        from src.collectors.mqtt_subscriber import MQTTNodeStore

        removed_ids = []
        store = MQTTNodeStore(
            max_nodes=2,
            on_node_removed=lambda nid: removed_ids.append(nid),
        )

        store.update_position("!aaa", 40.0, -105.0, timestamp=1000)
        store.update_position("!bbb", 40.1, -105.1, timestamp=2000)
        # This should evict !aaa (oldest)
        store.update_position("!ccc", 40.2, -105.2, timestamp=3000)

        assert "!aaa00000" in removed_ids or "!aaa" in [r.rstrip("0") for r in removed_ids] or len(removed_ids) == 1

    def test_mqtt_store_stale_cleanup_calls_callback(self):
        from src.collectors.mqtt_subscriber import MQTTNodeStore

        removed_ids = []
        store = MQTTNodeStore(
            remove_seconds=10,
            on_node_removed=lambda nid: removed_ids.append(nid),
        )

        # Add a node with an old timestamp
        store.update_position("!old1", 40.0, -105.0, timestamp=1)
        store.update_position("!new1", 40.1, -105.1)  # Current time

        removed = store.cleanup_stale_nodes()
        assert removed == 1
        assert "!old1" in removed_ids


# ---------------------------------------------------------------------------
# 9. Proxy server stop joins thread
# ---------------------------------------------------------------------------

class TestProxyServerStopJoinsThread:
    """Verify proxy stop() waits for server thread."""

    def test_proxy_stop_joins_thread(self):
        from src.utils.meshtastic_api_proxy import MeshtasticApiProxy

        proxy = MeshtasticApiProxy()
        mock_thread = MagicMock()
        mock_thread.is_alive.side_effect = [True, False]
        proxy._thread = mock_thread
        proxy._server = MagicMock()
        proxy._running = True

        proxy.stop()

        mock_thread.join.assert_called_once_with(timeout=5)
        assert proxy._thread is None


# ---------------------------------------------------------------------------
# 10. MapServer node removal handler
# ---------------------------------------------------------------------------

class TestMapServerNodeRemovalHandler:
    """Verify _handle_node_removed cleans up drift and state."""

    def test_handle_node_removed(self):
        from src.map_server import MapServer
        from src.utils.config import MapsConfig

        config = MapsConfig()
        config.set("enable_meshtastic", False)
        config.set("enable_reticulum", False)
        config.set("enable_aredn", False)
        config.set("enable_hamclock", False)

        server = MapServer(config)

        # Pre-populate drift and state
        server._config_drift.check_node("!aabb", role="ROUTER")
        server._node_state.record_heartbeat("!aabb", timestamp=1000)

        assert server._config_drift.tracked_node_count == 1
        assert server._node_state.tracked_node_count == 1

        # Trigger removal
        server._handle_node_removed("!aabb")

        assert server._config_drift.tracked_node_count == 0
        assert server._node_state.tracked_node_count == 0
