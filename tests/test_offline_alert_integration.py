"""Integration tests for offline node alert triggering.

Verifies that NodeStateTracker.check_offline() + AlertEngine.evaluate_offline()
work together correctly to detect and alert on offline nodes.
"""



from src.utils.alert_engine import AlertEngine
from src.utils.node_state import NodeStateTracker


class TestOfflineAlertWiring:
    """Test that offline detection triggers alerts."""

    # Use a short offline threshold for tests (matching the tracker threshold)
    OFFLINE_THRESHOLD = 60.0

    def test_offline_node_triggers_alert(self):
        """A node past the offline threshold should generate an alert."""
        tracker = NodeStateTracker(offline_threshold=self.OFFLINE_THRESHOLD)
        engine = AlertEngine()
        engine.clear_cooldowns()

        tracker.record_heartbeat("node1", timestamp=1000.0)

        # Check at t=1100 (100s later, past 60s threshold)
        newly_offline = tracker.check_offline(now=1100.0)
        assert "node1" in newly_offline

        # Feed to alert engine with matching threshold
        info = tracker.get_node_info("node1")
        assert info is not None
        alert = engine.evaluate_offline(
            "node1", last_seen=info["last_seen"],
            offline_threshold=self.OFFLINE_THRESHOLD, now=1100.0,
        )
        assert alert is not None
        assert alert.node_id == "node1"
        assert alert.rule_id == "node_offline"

    def test_online_node_no_alert(self):
        """A recently-seen node should not trigger offline alerts."""
        tracker = NodeStateTracker(offline_threshold=self.OFFLINE_THRESHOLD)
        engine = AlertEngine()
        engine.clear_cooldowns()

        tracker.record_heartbeat("node1", timestamp=1000.0)

        # Check at t=1030 (only 30s, within threshold)
        newly_offline = tracker.check_offline(now=1030.0)
        assert "node1" not in newly_offline

    def test_offline_alert_cooldown_prevents_repeated_firing(self):
        """Same node should not fire offline alert again within cooldown."""
        tracker = NodeStateTracker(offline_threshold=self.OFFLINE_THRESHOLD)
        engine = AlertEngine()
        engine.clear_cooldowns()

        tracker.record_heartbeat("node1", timestamp=1000.0)

        # First check triggers alert
        tracker.check_offline(now=1100.0)
        alert1 = engine.evaluate_offline(
            "node1", last_seen=1000.0,
            offline_threshold=self.OFFLINE_THRESHOLD, now=1100.0,
        )
        assert alert1 is not None

        # Second check within cooldown should not fire
        alert2 = engine.evaluate_offline(
            "node1", last_seen=1000.0,
            offline_threshold=self.OFFLINE_THRESHOLD, now=1200.0,
        )
        assert alert2 is None

    def test_multiple_offline_nodes(self):
        """Multiple nodes going offline should each trigger independent alerts."""
        tracker = NodeStateTracker(offline_threshold=self.OFFLINE_THRESHOLD)
        engine = AlertEngine()
        engine.clear_cooldowns()

        tracker.record_heartbeat("node1", timestamp=1000.0)
        tracker.record_heartbeat("node2", timestamp=1000.0)
        tracker.record_heartbeat("node3", timestamp=1010.0)

        newly_offline = tracker.check_offline(now=1100.0)
        assert len(newly_offline) >= 2

        alerts = []
        for node_id in newly_offline:
            info = tracker.get_node_info(node_id)
            if info:
                alert = engine.evaluate_offline(
                    node_id, last_seen=info["last_seen"],
                    offline_threshold=self.OFFLINE_THRESHOLD, now=1100.0,
                )
                if alert:
                    alerts.append(alert)

        assert len(alerts) >= 2
        alert_node_ids = {a.node_id for a in alerts}
        assert "node1" in alert_node_ids
        assert "node2" in alert_node_ids

    def test_node_comes_back_online_then_offline_again(self):
        """Node that recovers and goes offline again should fire a new alert
        after cooldown expires."""
        tracker = NodeStateTracker(offline_threshold=self.OFFLINE_THRESHOLD)
        engine = AlertEngine()
        engine.clear_cooldowns()

        # First offline cycle
        tracker.record_heartbeat("node1", timestamp=1000.0)
        tracker.check_offline(now=1100.0)
        alert1 = engine.evaluate_offline(
            "node1", last_seen=1000.0,
            offline_threshold=self.OFFLINE_THRESHOLD, now=1100.0,
        )
        assert alert1 is not None

        # Node comes back
        tracker.record_heartbeat("node1", timestamp=2000.0)

        # Goes offline again past cooldown (DEFAULT_COOLDOWN = 600s)
        tracker.check_offline(now=2700.0)
        alert2 = engine.evaluate_offline(
            "node1", last_seen=2000.0,
            offline_threshold=self.OFFLINE_THRESHOLD, now=2700.0,
        )
        assert alert2 is not None  # Past default cooldown (600s)


class TestOfflineAlertScope:
    """node_offline must fire only for owned/fleet nodes, not the tens of
    thousands of FOREIGN external-feed nodes the map also tracks (the 835k
    CRITICAL-alert churn). Scope = MapServer._offline_in_scope(node_id, owned)."""

    def test_owned_node_in_scope(self):
        from src.map_server import MapServer
        assert MapServer._offline_in_scope("!ebfa1b11", {"!ebfa1b11", "!32962f10"})

    def test_foreign_node_out_of_scope(self):
        from src.map_server import MapServer
        assert not MapServer._offline_in_scope("!02ed5754", {"!ebfa1b11", "!32962f10"})

    def test_empty_owned_is_unscoped_legacy(self):
        """Empty owned set = alert all (preserves legacy behavior / opt-out)."""
        from src.map_server import MapServer
        assert MapServer._offline_in_scope("!02ed5754", set())

    def test_scope_filter_drops_foreign_offline_nodes(self):
        """Integration: with owned scope, only owned offline nodes pass the
        filter the offline-check loop applies before firing an alert."""
        from src.map_server import MapServer
        owned = {"!ebfa1b11"}  # one fleet node
        tracker = NodeStateTracker(offline_threshold=60.0)
        tracker.record_heartbeat("!ebfa1b11", timestamp=1000.0)   # owned
        for fid in ("!02ed5754", "!f67092c8", "!9e77db3c"):       # foreign
            tracker.record_heartbeat(fid, timestamp=1000.0)
        newly_offline = tracker.check_offline(now=1100.0)
        assert len(newly_offline) == 4
        in_scope = [n for n in newly_offline
                    if MapServer._offline_in_scope(n, owned)]
        assert in_scope == ["!ebfa1b11"]   # only the owned node would alert
