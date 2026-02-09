"""Tests for ConfigDriftDetector — node configuration change detection."""

import time
import unittest

from src.utils.config_drift import (
    ConfigDriftDetector,
    DriftSeverity,
    TRACKED_FIELDS,
)


class TestConfigDriftDetector(unittest.TestCase):
    """Test config drift detection."""

    def test_first_observation_no_drift(self):
        """First observation of a node should produce no drifts."""
        detector = ConfigDriftDetector()
        drifts = detector.check_node("!aabb0001", role="CLIENT", hardware="TBEAM")
        self.assertEqual(drifts, [])
        self.assertEqual(detector.tracked_node_count, 1)

    def test_no_drift_on_same_values(self):
        """Repeated observations with same values should produce no drifts."""
        detector = ConfigDriftDetector()
        detector.check_node("!aabb0001", role="CLIENT")
        drifts = detector.check_node("!aabb0001", role="CLIENT")
        self.assertEqual(drifts, [])
        self.assertEqual(detector.total_drifts, 0)

    def test_role_change_detected(self):
        """Role change should produce a WARNING-severity drift."""
        detector = ConfigDriftDetector()
        detector.check_node("!aabb0001", role="CLIENT")
        drifts = detector.check_node("!aabb0001", role="ROUTER")
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0]["field"], "role")
        self.assertEqual(drifts[0]["old_value"], "CLIENT")
        self.assertEqual(drifts[0]["new_value"], "ROUTER")
        self.assertEqual(drifts[0]["severity"], "warning")

    def test_hardware_change_detected(self):
        """Hardware model change produces WARNING drift."""
        detector = ConfigDriftDetector()
        detector.check_node("!aabb0001", hardware="TBEAM")
        drifts = detector.check_node("!aabb0001", hardware="HELTEC_V3")
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0]["severity"], "warning")

    def test_name_change_is_info(self):
        """Name change is INFO severity."""
        detector = ConfigDriftDetector()
        detector.check_node("!aabb0001", name="OldName")
        drifts = detector.check_node("!aabb0001", name="NewName")
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0]["severity"], "info")

    def test_region_change_is_critical(self):
        """Region change is CRITICAL severity."""
        detector = ConfigDriftDetector()
        detector.check_node("!aabb0001", region="US")
        drifts = detector.check_node("!aabb0001", region="EU_868")
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0]["severity"], "critical")

    def test_modem_preset_change_is_critical(self):
        """Modem preset change is CRITICAL severity."""
        detector = ConfigDriftDetector()
        detector.check_node("!aabb0001", modem_preset="LONG_FAST")
        drifts = detector.check_node("!aabb0001", modem_preset="SHORT_FAST")
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0]["severity"], "critical")

    def test_multiple_changes_at_once(self):
        """Multiple field changes in one check produce multiple drifts."""
        detector = ConfigDriftDetector()
        detector.check_node("!aabb0001", role="CLIENT", name="Alpha")
        drifts = detector.check_node("!aabb0001", role="ROUTER", name="Beta")
        self.assertEqual(len(drifts), 2)
        fields = {d["field"] for d in drifts}
        self.assertEqual(fields, {"role", "name"})

    def test_untracked_fields_ignored(self):
        """Fields not in TRACKED_FIELDS should be ignored."""
        detector = ConfigDriftDetector()
        detector.check_node("!aabb0001", unknown_field="value1")
        drifts = detector.check_node("!aabb0001", unknown_field="value2")
        self.assertEqual(drifts, [])

    def test_none_values_ignored(self):
        """None values should not be stored or compared."""
        detector = ConfigDriftDetector()
        detector.check_node("!aabb0001", role="CLIENT")
        drifts = detector.check_node("!aabb0001", role=None)
        self.assertEqual(drifts, [])  # None skipped, no drift

    def test_empty_fields_no_snapshot(self):
        """If all fields are None/untracked, no snapshot is created."""
        detector = ConfigDriftDetector()
        drifts = detector.check_node("!aabb0001")
        self.assertEqual(drifts, [])
        self.assertEqual(detector.tracked_node_count, 0)

    def test_get_node_snapshot(self):
        """Snapshot returns stored config values."""
        detector = ConfigDriftDetector()
        detector.check_node("!aabb0001", role="CLIENT", hardware="TBEAM")
        snap = detector.get_node_snapshot("!aabb0001")
        self.assertIsNotNone(snap)
        self.assertEqual(snap["role"], "CLIENT")
        self.assertEqual(snap["hardware"], "TBEAM")

    def test_get_node_snapshot_unknown(self):
        """Snapshot for unknown node returns None."""
        detector = ConfigDriftDetector()
        self.assertIsNone(detector.get_node_snapshot("!unknown"))

    def test_get_node_drift_history(self):
        """Drift history records all changes for a node."""
        detector = ConfigDriftDetector()
        detector.check_node("!aabb0001", role="CLIENT")
        detector.check_node("!aabb0001", role="ROUTER")
        detector.check_node("!aabb0001", role="TRACKER")
        history = detector.get_node_drift_history("!aabb0001")
        self.assertEqual(len(history), 2)

    def test_get_all_drifts(self):
        """get_all_drifts returns drifts across all nodes."""
        detector = ConfigDriftDetector()
        detector.check_node("!node1", role="CLIENT")
        detector.check_node("!node2", role="ROUTER")
        detector.check_node("!node1", role="ROUTER")
        detector.check_node("!node2", role="CLIENT")
        all_drifts = detector.get_all_drifts()
        self.assertEqual(len(all_drifts), 2)

    def test_get_all_drifts_filtered_by_severity(self):
        """Filter drifts by severity level."""
        detector = ConfigDriftDetector()
        detector.check_node("!node1", role="CLIENT", name="A")
        detector.check_node("!node1", role="ROUTER", name="B")
        warning_only = detector.get_all_drifts(severity="warning")
        self.assertEqual(len(warning_only), 1)
        self.assertEqual(warning_only[0]["field"], "role")

    def test_get_summary(self):
        """Summary includes tracked counts and recent drifts."""
        detector = ConfigDriftDetector()
        detector.check_node("!node1", role="CLIENT")
        detector.check_node("!node1", role="ROUTER")
        summary = detector.get_summary()
        self.assertEqual(summary["tracked_nodes"], 1)
        self.assertEqual(summary["nodes_with_drift"], 1)
        self.assertEqual(summary["total_drifts"], 1)
        self.assertGreater(len(summary["recent_drifts"]), 0)

    def test_drift_callback(self):
        """on_drift callback is called when drift is detected."""
        callback_data = []

        def on_drift(node_id, drifts):
            callback_data.append((node_id, drifts))

        detector = ConfigDriftDetector(on_drift=on_drift)
        detector.check_node("!aabb0001", role="CLIENT")
        detector.check_node("!aabb0001", role="ROUTER")
        self.assertEqual(len(callback_data), 1)
        self.assertEqual(callback_data[0][0], "!aabb0001")

    def test_bad_callback_does_not_crash(self):
        """A failing callback should not crash the detector."""
        def bad_callback(node_id, drifts):
            raise RuntimeError("Callback error")

        detector = ConfigDriftDetector(on_drift=bad_callback)
        detector.check_node("!aabb0001", role="CLIENT")
        # Should not raise
        drifts = detector.check_node("!aabb0001", role="ROUTER")
        self.assertEqual(len(drifts), 1)

    def test_max_history_per_node(self):
        """Drift history is bounded per node."""
        detector = ConfigDriftDetector(max_history=3)
        detector.check_node("!node1", role="A")
        for i in range(10):
            detector.check_node("!node1", role=f"R{i}")
        history = detector.get_node_drift_history("!node1")
        self.assertEqual(len(history), 3)

    def test_max_tracked_nodes_eviction(self):
        """Evicts oldest node when max_nodes exceeded."""
        detector = ConfigDriftDetector(max_nodes=3)
        detector.check_node("!node1", role="A")
        detector.check_node("!node2", role="B")
        detector.check_node("!node3", role="C")
        # This should evict !node1
        detector.check_node("!node4", role="D")
        self.assertEqual(detector.tracked_node_count, 3)
        self.assertIsNone(detector.get_node_snapshot("!node1"))

    def test_snapshot_updated_after_drift(self):
        """Snapshot reflects the latest values after drift."""
        detector = ConfigDriftDetector()
        detector.check_node("!node1", role="CLIENT")
        detector.check_node("!node1", role="ROUTER")
        snap = detector.get_node_snapshot("!node1")
        self.assertEqual(snap["role"], "ROUTER")

    def test_tracked_fields_coverage(self):
        """All declared tracked fields have a severity."""
        for field, severity in TRACKED_FIELDS.items():
            self.assertIsInstance(severity, DriftSeverity)

    def test_drift_severity_values(self):
        """DriftSeverity enum values are strings."""
        self.assertEqual(DriftSeverity.INFO.value, "info")
        self.assertEqual(DriftSeverity.WARNING.value, "warning")
        self.assertEqual(DriftSeverity.CRITICAL.value, "critical")


if __name__ == "__main__":
    unittest.main()
