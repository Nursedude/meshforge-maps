"""Tests for NodeStateTracker — node connectivity state machine."""

import time
import unittest

from src.utils.node_state import (
    NodeState,
    NodeStateEntry,
    NodeStateTracker,
)


class TestNodeStateEntry(unittest.TestCase):
    """Test internal state entry."""

    def test_initial_state(self):
        entry = NodeStateEntry("!node1", 1000.0)
        self.assertEqual(entry.state, NodeState.NEW)
        self.assertEqual(list(entry.heartbeats), [1000.0])
        self.assertEqual(entry.first_seen, 1000.0)
        self.assertEqual(entry.transition_count, 0)

    def test_add_heartbeat(self):
        entry = NodeStateEntry("!node1", 1000.0)
        entry.add_heartbeat(1300.0, max_window=20)
        self.assertEqual(len(entry.heartbeats), 2)
        self.assertEqual(entry.last_seen, 1300.0)

    def test_heartbeat_window_trimming(self):
        entry = NodeStateEntry("!node1", 1000.0, max_window=10)
        for i in range(25):
            entry.add_heartbeat(1000.0 + (i + 1) * 100, max_window=10)
        self.assertEqual(len(entry.heartbeats), 10)

    def test_average_interval(self):
        entry = NodeStateEntry("!node1", 1000.0)
        entry.add_heartbeat(1300.0, 20)
        entry.add_heartbeat(1600.0, 20)
        avg = entry.average_interval()
        self.assertEqual(avg, 300.0)

    def test_average_interval_single_heartbeat(self):
        entry = NodeStateEntry("!node1", 1000.0)
        self.assertIsNone(entry.average_interval())

    def test_gap_ratio_no_gaps(self):
        """Regular heartbeats produce gap_ratio 0."""
        entry = NodeStateEntry("!node1", 1000.0)
        for i in range(5):
            entry.add_heartbeat(1000.0 + (i + 1) * 300, 20)
        # Expected interval 300s, gap threshold 600s, all intervals are 300s
        self.assertEqual(entry.gap_ratio(300.0), 0.0)

    def test_gap_ratio_all_gaps(self):
        """All long intervals produce gap_ratio 1."""
        entry = NodeStateEntry("!node1", 1000.0)
        for i in range(5):
            entry.add_heartbeat(1000.0 + (i + 1) * 1000, 20)
        # Expected 300, gap_threshold 600, all intervals are 1000 > 600
        self.assertEqual(entry.gap_ratio(300.0), 1.0)

    def test_gap_ratio_mixed(self):
        """Mixed intervals produce fractional gap ratio."""
        entry = NodeStateEntry("!node1", 1000.0)
        entry.add_heartbeat(1300.0, 20)  # 300s, not a gap
        entry.add_heartbeat(2500.0, 20)  # 1200s, IS a gap
        entry.add_heartbeat(2800.0, 20)  # 300s, not a gap
        # 3 intervals: 300, 1200, 300 — 1 gap out of 3
        self.assertAlmostEqual(entry.gap_ratio(300.0), 1 / 3, places=2)

    def test_to_dict(self):
        entry = NodeStateEntry("!node1", 1000.0)
        entry.add_heartbeat(1300.0, 20)
        d = entry.to_dict()
        self.assertEqual(d["node_id"], "!node1")
        self.assertEqual(d["state"], "new")
        self.assertEqual(d["heartbeat_count"], 2)
        self.assertEqual(d["average_interval"], 300.0)


class TestNodeStateTracker(unittest.TestCase):
    """Test the full state machine tracker."""

    def test_first_heartbeat_returns_new(self):
        tracker = NodeStateTracker()
        old, new = tracker.record_heartbeat("!node1", timestamp=1000.0)
        self.assertEqual(old, NodeState.NEW)
        self.assertEqual(new, NodeState.NEW)

    def test_stable_after_regular_heartbeats(self):
        """Node transitions to STABLE after regular heartbeats."""
        tracker = NodeStateTracker(expected_interval=300)
        t = 1000.0
        for i in range(5):
            tracker.record_heartbeat("!node1", timestamp=t + i * 300)
        state = tracker.get_node_state("!node1")
        self.assertEqual(state, NodeState.STABLE)

    def test_intermittent_after_irregular_heartbeats(self):
        """Node transitions to INTERMITTENT with large gaps."""
        tracker = NodeStateTracker(expected_interval=300, intermittent_ratio=0.5)
        # Regular heartbeats at 300s interval, then huge gaps
        tracker.record_heartbeat("!node1", timestamp=1000.0)
        tracker.record_heartbeat("!node1", timestamp=1300.0)
        tracker.record_heartbeat("!node1", timestamp=3000.0)  # gap
        tracker.record_heartbeat("!node1", timestamp=5000.0)  # gap
        tracker.record_heartbeat("!node1", timestamp=7000.0)  # gap
        state = tracker.get_node_state("!node1")
        self.assertEqual(state, NodeState.INTERMITTENT)

    def test_check_offline(self):
        """Nodes go OFFLINE when not seen within threshold."""
        tracker = NodeStateTracker(offline_threshold=600)
        tracker.record_heartbeat("!node1", timestamp=1000.0)
        tracker.record_heartbeat("!node1", timestamp=1300.0)
        tracker.record_heartbeat("!node1", timestamp=1600.0)
        # Check at t=2500 (900s since last heartbeat > 600s threshold)
        offline_nodes = tracker.check_offline(now=2500.0)
        self.assertEqual(offline_nodes, ["!node1"])
        self.assertEqual(tracker.get_node_state("!node1"), NodeState.OFFLINE)

    def test_check_offline_skips_already_offline(self):
        """Already-OFFLINE nodes are not transitioned again."""
        tracker = NodeStateTracker(offline_threshold=600)
        tracker.record_heartbeat("!node1", timestamp=1000.0)
        tracker.check_offline(now=2000.0)  # Goes offline
        offline_nodes = tracker.check_offline(now=3000.0)  # Should not repeat
        self.assertEqual(offline_nodes, [])

    def test_offline_to_new_on_heartbeat(self):
        """OFFLINE node receiving a heartbeat transitions back."""
        tracker = NodeStateTracker(offline_threshold=600)
        tracker.record_heartbeat("!node1", timestamp=1000.0)
        tracker.check_offline(now=2000.0)
        self.assertEqual(tracker.get_node_state("!node1"), NodeState.OFFLINE)
        # New heartbeat comes in
        old, new = tracker.record_heartbeat("!node1", timestamp=2100.0)
        # With enough heartbeats it'll eventually be stable, but with just
        # 2 heartbeats it stays NEW (needs >= 3 for classification)
        self.assertIn(new, [NodeState.NEW, NodeState.STABLE, NodeState.INTERMITTENT])

    def test_transition_callback(self):
        """Callback fires on state transitions."""
        transitions = []

        def on_transition(node_id, old_state, new_state):
            transitions.append((node_id, old_state, new_state))

        tracker = NodeStateTracker(
            expected_interval=300,
            offline_threshold=600,
            on_transition=on_transition,
        )
        # Build up enough heartbeats to go from NEW to STABLE
        for i in range(5):
            tracker.record_heartbeat("!node1", timestamp=1000 + i * 300)
        # Should have transitioned NEW -> STABLE
        stable_transitions = [t for t in transitions if t[2] == NodeState.STABLE]
        self.assertGreater(len(stable_transitions), 0)

    def test_bad_callback_does_not_crash(self):
        """Failing callback should not crash the tracker."""
        def bad_callback(node_id, old_state, new_state):
            raise RuntimeError("callback error")

        tracker = NodeStateTracker(on_transition=bad_callback, expected_interval=300)
        for i in range(5):
            tracker.record_heartbeat("!node1", timestamp=1000 + i * 300)
        # Should not raise

    def test_get_node_state_unknown(self):
        """Unknown node returns None."""
        tracker = NodeStateTracker()
        self.assertIsNone(tracker.get_node_state("!unknown"))

    def test_get_node_info(self):
        """get_node_info returns dict with state details."""
        tracker = NodeStateTracker()
        tracker.record_heartbeat("!node1", timestamp=1000.0)
        info = tracker.get_node_info("!node1")
        self.assertIsNotNone(info)
        self.assertEqual(info["node_id"], "!node1")
        self.assertEqual(info["state"], "new")

    def test_get_node_info_unknown(self):
        """get_node_info for unknown node returns None."""
        tracker = NodeStateTracker()
        self.assertIsNone(tracker.get_node_info("!unknown"))

    def test_get_all_states(self):
        """get_all_states returns dict of all node states."""
        tracker = NodeStateTracker()
        tracker.record_heartbeat("!node1", timestamp=1000.0)
        tracker.record_heartbeat("!node2", timestamp=1000.0)
        states = tracker.get_all_states()
        self.assertEqual(len(states), 2)
        self.assertEqual(states["!node1"], "new")

    def test_get_summary(self):
        """Summary includes state counts."""
        tracker = NodeStateTracker(expected_interval=300)
        for i in range(5):
            tracker.record_heartbeat("!node1", timestamp=1000 + i * 300)
        tracker.record_heartbeat("!node2", timestamp=1000.0)
        summary = tracker.get_summary()
        self.assertEqual(summary["tracked_nodes"], 2)
        self.assertIn("new", summary["states"])
        self.assertIn("stable", summary["states"])

    def test_get_nodes_by_state(self):
        """Filter nodes by their current state."""
        tracker = NodeStateTracker()
        tracker.record_heartbeat("!node1", timestamp=1000.0)
        tracker.record_heartbeat("!node2", timestamp=1000.0)
        new_nodes = tracker.get_nodes_by_state(NodeState.NEW)
        self.assertEqual(len(new_nodes), 2)

    def test_total_transitions(self):
        """total_transitions increments on state changes."""
        tracker = NodeStateTracker(expected_interval=300)
        for i in range(5):
            tracker.record_heartbeat("!node1", timestamp=1000 + i * 300)
        # At least one transition: NEW -> STABLE
        self.assertGreater(tracker.total_transitions, 0)

    def test_max_nodes_eviction(self):
        """Oldest node evicted when max_nodes exceeded."""
        tracker = NodeStateTracker(max_nodes=3)
        tracker.record_heartbeat("!node1", timestamp=1000.0)
        tracker.record_heartbeat("!node2", timestamp=1001.0)
        tracker.record_heartbeat("!node3", timestamp=1002.0)
        tracker.record_heartbeat("!node4", timestamp=1003.0)
        self.assertEqual(tracker.tracked_node_count, 3)
        self.assertIsNone(tracker.get_node_state("!node1"))

    def test_offline_check_callback(self):
        """check_offline fires transition callback."""
        transitions = []

        def on_transition(node_id, old_state, new_state):
            transitions.append((node_id, old_state, new_state))

        tracker = NodeStateTracker(
            offline_threshold=600,
            on_transition=on_transition,
        )
        tracker.record_heartbeat("!node1", timestamp=1000.0)
        tracker.check_offline(now=2000.0)
        offline_transitions = [t for t in transitions if t[2] == NodeState.OFFLINE]
        self.assertEqual(len(offline_transitions), 1)


if __name__ == "__main__":
    unittest.main()
