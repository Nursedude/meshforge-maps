"""Integration tests for ConfigDriftDetector.

Tests the full pipeline: EventBus events → ConfigDriftDetector → HTTP API,
verifying that components are wired correctly and work end-to-end.
"""

import json
import time
import unittest
from unittest.mock import MagicMock, patch

import pytest

from src.map_server import (
    MapRequestHandler,
    MapServer,
    MapServerContext,
    MeshForgeHTTPServer,
)
from src.utils.config import MapsConfig
from src.utils.config_drift import (
    ConfigDriftDetector,
    DriftSeverity,
    TRACKED_FIELDS,
    _normalize_value,
)
from src.utils.event_bus import EventBus, EventType, NodeEvent


# ---------------------------------------------------------------------------
# Unit: _normalize_value edge cases
# ---------------------------------------------------------------------------

class TestNormalizeValue:
    """Edge cases for the value normalizer used in drift comparison."""

    def test_int_and_float_equal(self):
        assert _normalize_value(1) == _normalize_value(1.0)

    def test_float_with_fractional_part(self):
        assert _normalize_value(1.5) == "1.5"

    def test_string_passthrough(self):
        assert _normalize_value("ROUTER") == "ROUTER"

    def test_bool_normalized(self):
        assert _normalize_value(True) == "True"

    def test_none_normalized(self):
        assert _normalize_value(None) == "None"

    def test_zero(self):
        assert _normalize_value(0) == _normalize_value(0.0)


# ---------------------------------------------------------------------------
# Integration: EventBus → ConfigDriftDetector
# ---------------------------------------------------------------------------

class TestEventBusDriftIntegration:
    """Test that EventBus NODE_INFO events feed into ConfigDriftDetector."""

    def test_node_info_event_creates_snapshot(self):
        """A NODE_INFO event should create a config snapshot in the detector."""
        bus = EventBus()
        detector = ConfigDriftDetector()

        def on_info(event):
            if isinstance(event, NodeEvent):
                data = event.data or {}
                detector.check_node(
                    event.node_id,
                    **{k: v for k, v in data.items() if v is not None},
                )

        bus.subscribe(EventType.NODE_INFO, on_info)

        event = NodeEvent.info("!aabb0001", role="CLIENT", hardware="TBEAM")
        bus.publish(event)

        snap = detector.get_node_snapshot("!aabb0001")
        assert snap is not None
        assert snap["role"] == "CLIENT"
        assert snap["hardware"] == "TBEAM"

    def test_sequential_events_detect_drift(self):
        """Two NODE_INFO events with different values should produce a drift."""
        bus = EventBus()
        detector = ConfigDriftDetector()
        detected_drifts = []

        def on_info(event):
            if isinstance(event, NodeEvent):
                data = event.data or {}
                drifts = detector.check_node(
                    event.node_id,
                    **{k: v for k, v in data.items() if v is not None},
                )
                detected_drifts.extend(drifts)

        bus.subscribe(EventType.NODE_INFO, on_info)

        # First observation — no drift
        bus.publish(NodeEvent.info("!aabb0001", role="CLIENT", hardware="TBEAM"))
        assert len(detected_drifts) == 0

        # Second observation — role changed
        bus.publish(NodeEvent.info("!aabb0001", role="ROUTER", hardware="TBEAM"))
        assert len(detected_drifts) == 1
        assert detected_drifts[0]["field"] == "role"
        assert detected_drifts[0]["old_value"] == "CLIENT"
        assert detected_drifts[0]["new_value"] == "ROUTER"
        assert detected_drifts[0]["severity"] == "warning"

    def test_multi_field_drift_from_events(self):
        """Multiple fields changing in a single event produce multiple drifts."""
        bus = EventBus()
        detector = ConfigDriftDetector()
        detected_drifts = []

        def on_info(event):
            if isinstance(event, NodeEvent):
                data = event.data or {}
                drifts = detector.check_node(
                    event.node_id,
                    **{k: v for k, v in data.items() if v is not None},
                )
                detected_drifts.extend(drifts)

        bus.subscribe(EventType.NODE_INFO, on_info)
        bus.publish(NodeEvent.info(
            "!node1", role="CLIENT", region="US", name="Alpha",
        ))
        bus.publish(NodeEvent.info(
            "!node1", role="ROUTER", region="EU_868", name="Beta",
        ))

        assert len(detected_drifts) == 3
        fields = {d["field"] for d in detected_drifts}
        assert fields == {"role", "region", "name"}
        severities = {d["field"]: d["severity"] for d in detected_drifts}
        assert severities["role"] == "warning"
        assert severities["region"] == "critical"
        assert severities["name"] == "info"

    def test_callback_receives_drifts_from_event_pipeline(self):
        """on_drift callback fires when drift is triggered via event bus."""
        bus = EventBus()
        callback_log = []

        def on_drift_cb(node_id, drifts):
            callback_log.append((node_id, drifts))

        detector = ConfigDriftDetector(on_drift=on_drift_cb)

        def on_info(event):
            if isinstance(event, NodeEvent):
                data = event.data or {}
                detector.check_node(
                    event.node_id,
                    **{k: v for k, v in data.items() if v is not None},
                )

        bus.subscribe(EventType.NODE_INFO, on_info)

        bus.publish(NodeEvent.info("!abc123", role="CLIENT"))
        bus.publish(NodeEvent.info("!abc123", role="ROUTER"))

        assert len(callback_log) == 1
        assert callback_log[0][0] == "!abc123"
        assert callback_log[0][1][0]["field"] == "role"

    def test_non_node_events_ignored(self):
        """Non-NodeEvent events should not crash the drift handler."""
        bus = EventBus()
        detector = ConfigDriftDetector()

        def on_info(event):
            if isinstance(event, NodeEvent):
                data = event.data or {}
                detector.check_node(
                    event.node_id,
                    **{k: v for k, v in data.items() if v is not None},
                )

        bus.subscribe(EventType.NODE_INFO, on_info)

        # Publish a base Event (not NodeEvent) — should not crash
        from src.utils.event_bus import Event
        bus.publish(Event(event_type=EventType.NODE_INFO))

        assert detector.tracked_node_count == 0

    def test_untracked_fields_in_event_data_ignored(self):
        """Event data containing non-tracked fields should be ignored."""
        bus = EventBus()
        detector = ConfigDriftDetector()

        def on_info(event):
            if isinstance(event, NodeEvent):
                data = event.data or {}
                detector.check_node(
                    event.node_id,
                    **{k: v for k, v in data.items() if v is not None},
                )

        bus.subscribe(EventType.NODE_INFO, on_info)
        bus.publish(NodeEvent.info("!aabb0001", battery_level=87, snr=9.5))
        assert detector.tracked_node_count == 0

    def test_multiple_nodes_tracked_independently(self):
        """Drift detection is per-node; changes to one don't affect another."""
        bus = EventBus()
        detector = ConfigDriftDetector()
        all_drifts = []

        def on_info(event):
            if isinstance(event, NodeEvent):
                data = event.data or {}
                drifts = detector.check_node(
                    event.node_id,
                    **{k: v for k, v in data.items() if v is not None},
                )
                all_drifts.extend(drifts)

        bus.subscribe(EventType.NODE_INFO, on_info)

        bus.publish(NodeEvent.info("!node1", role="CLIENT"))
        bus.publish(NodeEvent.info("!node2", role="ROUTER"))
        # Change node1 only
        bus.publish(NodeEvent.info("!node1", role="ROUTER"))

        assert len(all_drifts) == 1
        assert all_drifts[0]["node_id"] == "!node1"
        assert detector.tracked_node_count == 2


# ---------------------------------------------------------------------------
# Integration: ConfigDriftDetector + node eviction
# ---------------------------------------------------------------------------

class TestDriftEvictionIntegration:
    """Test that node removal propagates to drift detector correctly."""

    def test_remove_cleans_snapshot_and_history(self):
        """Removing a node clears both snapshot and drift history."""
        detector = ConfigDriftDetector()
        detector.check_node("!node1", role="CLIENT")
        detector.check_node("!node1", role="ROUTER")

        assert detector.get_node_snapshot("!node1") is not None
        assert len(detector.get_node_drift_history("!node1")) == 1

        detector.remove_node("!node1")
        assert detector.get_node_snapshot("!node1") is None
        assert detector.get_node_drift_history("!node1") == []

    def test_remove_nonexistent_node_is_safe(self):
        """Removing a node that doesn't exist should not raise."""
        detector = ConfigDriftDetector()
        detector.remove_node("!nonexistent")  # should not raise

    def test_eviction_on_max_nodes(self):
        """When max_nodes is exceeded, oldest node is evicted."""
        detector = ConfigDriftDetector(max_nodes=2)
        detector.check_node("!old", role="CLIENT")
        detector.check_node("!middle", role="CLIENT")
        # This should evict !old
        detector.check_node("!new", role="CLIENT")

        assert detector.get_node_snapshot("!old") is None
        assert detector.get_node_snapshot("!middle") is not None
        assert detector.get_node_snapshot("!new") is not None
        assert detector.tracked_node_count == 2


# ---------------------------------------------------------------------------
# Integration: get_all_drifts filtering
# ---------------------------------------------------------------------------

class TestDriftQueryFiltering:
    """Test get_all_drifts with since/severity filters — mirrors HTTP API."""

    def test_filter_by_severity(self):
        detector = ConfigDriftDetector()
        detector.check_node("!n1", role="CLIENT", name="A", region="US")
        detector.check_node("!n1", role="ROUTER", name="B", region="EU_868")

        critical = detector.get_all_drifts(severity="critical")
        assert all(d["severity"] == "critical" for d in critical)
        assert len(critical) == 1
        assert critical[0]["field"] == "region"

        warning = detector.get_all_drifts(severity="warning")
        assert all(d["severity"] == "warning" for d in warning)
        assert len(warning) == 1

        info = detector.get_all_drifts(severity="info")
        assert all(d["severity"] == "info" for d in info)
        assert len(info) == 1

    def test_filter_by_since_timestamp(self):
        detector = ConfigDriftDetector()
        detector.check_node("!n1", role="CLIENT")
        before = time.time()
        detector.check_node("!n1", role="ROUTER")
        after = time.time()

        # All drifts since before should include our drift
        drifts = detector.get_all_drifts(since=before - 1)
        assert len(drifts) == 1

        # Filtering with a future timestamp should return nothing
        drifts = detector.get_all_drifts(since=after + 100)
        assert len(drifts) == 0

    def test_combined_filters(self):
        detector = ConfigDriftDetector()
        detector.check_node("!n1", role="CLIENT", region="US")
        before = time.time()
        detector.check_node("!n1", role="ROUTER", region="EU_868")

        # Only critical since before
        drifts = detector.get_all_drifts(since=before - 1, severity="critical")
        assert len(drifts) == 1
        assert drifts[0]["field"] == "region"

    def test_drifts_sorted_newest_first(self):
        detector = ConfigDriftDetector()
        detector.check_node("!n1", role="A")
        detector.check_node("!n1", role="B")
        time.sleep(0.01)
        detector.check_node("!n1", role="C")

        drifts = detector.get_all_drifts()
        assert len(drifts) == 2
        assert drifts[0]["timestamp"] >= drifts[1]["timestamp"]


# ---------------------------------------------------------------------------
# Integration: HTTP API endpoints (live server)
# ---------------------------------------------------------------------------

class TestConfigDriftHTTPEndpoints:
    """Integration tests for config drift HTTP API endpoints.

    Starts a real MapServer, feeds data into the drift detector, and
    verifies the API responses.
    """

    @pytest.fixture(autouse=True)
    def _setup_server(self, tmp_path):
        """Start a real server for HTTP integration testing."""
        from urllib.request import urlopen, Request
        from urllib.error import HTTPError

        self.urlopen = urlopen
        self.Request = Request
        self.HTTPError = HTTPError

        config = MapsConfig(config_path=tmp_path / "settings.json")
        config.set("http_port", 19850)
        config.set("enable_meshtastic", False)
        config.set("enable_reticulum", False)
        config.set("enable_hamclock", False)
        config.set("enable_aredn", False)
        self.server = MapServer(config)
        assert self.server.start() is True
        self.base = f"http://127.0.0.1:{self.server.port}"
        time.sleep(0.1)
        yield
        self.server.stop()

    def _get_json(self, path):
        req = self.Request(self.base + path, headers={"Accept": "application/json"})
        with self.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())

    def test_config_drift_summary_empty(self):
        """Summary endpoint returns zero counts when no drifts have occurred."""
        data = self._get_json("/api/config-drift/summary")
        assert data["tracked_nodes"] == 0
        assert data["total_drifts"] == 0
        assert data["nodes_with_drift"] == 0
        assert data["recent_drifts"] == []

    def test_config_drift_events_empty(self):
        """Events endpoint returns empty list when no drifts have occurred."""
        data = self._get_json("/api/config-drift")
        assert "drifts" in data
        assert data["drifts"] == []
        assert data["total"] == 0

    def test_config_drift_after_feeding_events(self):
        """Drift detected after NODE_INFO events are published to the bus."""
        # Access the internal drift detector and feed it data directly
        detector = self.server._config_drift

        detector.check_node("!testnode", role="CLIENT", hardware="TBEAM")
        detector.check_node("!testnode", role="ROUTER", hardware="TBEAM")

        # Check summary
        summary = self._get_json("/api/config-drift/summary")
        assert summary["tracked_nodes"] == 1
        assert summary["total_drifts"] == 1
        assert summary["nodes_with_drift"] == 1
        assert len(summary["recent_drifts"]) == 1

        # Check events
        events = self._get_json("/api/config-drift")
        assert events["total"] == 1
        assert len(events["drifts"]) == 1
        drift = events["drifts"][0]
        assert drift["node_id"] == "!testnode"
        assert drift["field"] == "role"
        assert drift["old_value"] == "CLIENT"
        assert drift["new_value"] == "ROUTER"
        assert drift["severity"] == "warning"

    def test_config_drift_severity_filter(self):
        """Events endpoint filters by severity query parameter."""
        detector = self.server._config_drift

        detector.check_node("!n1", role="CLIENT", region="US", name="Alpha")
        detector.check_node("!n1", role="ROUTER", region="EU_868", name="Beta")

        # Filter critical only
        critical = self._get_json("/api/config-drift?severity=critical")
        assert all(d["severity"] == "critical" for d in critical["drifts"])
        assert critical["total"] >= 1

        # Filter warning only
        warning = self._get_json("/api/config-drift?severity=warning")
        assert all(d["severity"] == "warning" for d in warning["drifts"])

        # Filter info only
        info = self._get_json("/api/config-drift?severity=info")
        assert all(d["severity"] == "info" for d in info["drifts"])

    def test_config_drift_via_event_bus(self):
        """Full pipeline: EventBus → detector → HTTP API."""
        bus = self.server._aggregator.event_bus

        # Publish initial node info
        bus.publish(NodeEvent.info("!evtnode", role="CLIENT", hardware="HELTEC_V3"))
        # Publish updated node info with changed role
        bus.publish(NodeEvent.info("!evtnode", role="ROUTER", hardware="HELTEC_V3"))

        # Verify via HTTP
        summary = self._get_json("/api/config-drift/summary")
        assert summary["tracked_nodes"] >= 1
        assert summary["total_drifts"] >= 1

    def test_config_drift_multiple_nodes(self):
        """Multiple nodes with drifts are tracked independently."""
        detector = self.server._config_drift

        detector.check_node("!node_a", role="CLIENT")
        detector.check_node("!node_b", role="ROUTER")
        detector.check_node("!node_a", role="ROUTER")
        detector.check_node("!node_b", role="CLIENT")

        summary = self._get_json("/api/config-drift/summary")
        assert summary["tracked_nodes"] == 2
        assert summary["nodes_with_drift"] == 2
        assert summary["total_drifts"] == 2

        events = self._get_json("/api/config-drift")
        assert events["total"] == 2

    def test_config_drift_critical_severity_events(self):
        """Critical drifts (region, modem_preset) are properly reported."""
        detector = self.server._config_drift

        detector.check_node("!crit1", region="US", modem_preset="LONG_FAST")
        detector.check_node("!crit1", region="EU_868", modem_preset="SHORT_FAST")

        events = self._get_json("/api/config-drift")
        assert events["total"] == 2
        assert all(d["severity"] == "critical" for d in events["drifts"])

    def test_summary_recent_drifts_bounded(self):
        """Summary recent_drifts list is bounded (max 10)."""
        detector = self.server._config_drift

        # Create many drifts across multiple nodes
        for i in range(20):
            node_id = f"!node{i:04d}"
            detector.check_node(node_id, role="CLIENT")
            detector.check_node(node_id, role="ROUTER")

        summary = self._get_json("/api/config-drift/summary")
        assert len(summary["recent_drifts"]) <= 10


# ---------------------------------------------------------------------------
# Thread safety: concurrent drift detection
# ---------------------------------------------------------------------------

class TestDriftConcurrency:
    """Verify thread-safe access to the drift detector."""

    def test_concurrent_check_node(self):
        """Multiple threads calling check_node concurrently should not crash."""
        import threading

        detector = ConfigDriftDetector()
        errors = []

        def worker(tid):
            try:
                node_id = f"!thread{tid}"
                detector.check_node(node_id, role="CLIENT")
                for i in range(10):
                    detector.check_node(node_id, role=f"ROLE_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert detector.tracked_node_count == 10
        # Each thread did 10 role changes → 10 drifts per thread
        assert detector.total_drifts == 100

    def test_concurrent_read_and_write(self):
        """Readers and writers operating concurrently should not corrupt state."""
        import threading

        detector = ConfigDriftDetector()
        errors = []

        def writer():
            try:
                for i in range(50):
                    detector.check_node("!shared", role=f"R{i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(50):
                    detector.get_summary()
                    detector.get_all_drifts()
                    detector.get_node_snapshot("!shared")
                    detector.get_node_drift_history("!shared")
            except Exception as e:
                errors.append(e)

        threads = []
        for _ in range(3):
            threads.append(threading.Thread(target=writer))
            threads.append(threading.Thread(target=reader))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
