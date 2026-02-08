"""Tests for MQTT subscriber node store and topology."""

import time
import pytest

from src.collectors.mqtt_subscriber import MQTTNodeStore, MQTTSubscriber


class TestMQTTNodeStore:
    """Tests for the thread-safe node store."""

    def test_update_position(self):
        store = MQTTNodeStore()
        store.update_position("!abc123", 35.6895, 139.6917, altitude=40)
        nodes = store.get_all_nodes()
        assert len(nodes) == 1
        assert nodes[0]["latitude"] == 35.6895
        assert nodes[0]["longitude"] == 139.6917
        assert nodes[0]["altitude"] == 40

    def test_update_nodeinfo(self):
        store = MQTTNodeStore()
        store.update_position("!abc", 10.0, 20.0)
        store.update_nodeinfo("!abc", long_name="Test Node", hw_model="TBEAM")
        nodes = store.get_all_nodes()
        assert nodes[0]["name"] == "Test Node"
        assert nodes[0]["hardware"] == "TBEAM"

    def test_update_telemetry(self):
        store = MQTTNodeStore()
        store.update_position("!abc", 10.0, 20.0)
        store.update_telemetry("!abc", battery=87, voltage=3.7)
        nodes = store.get_all_nodes()
        assert nodes[0]["battery"] == 87
        assert nodes[0]["voltage"] == 3.7

    def test_filters_nodes_without_coords(self):
        store = MQTTNodeStore()
        store.update_nodeinfo("!nocoords", long_name="No Position")
        nodes = store.get_all_nodes()
        assert len(nodes) == 0

    def test_filters_invalid_coords(self):
        store = MQTTNodeStore()
        store.update_position("!bad", 999, 999)
        nodes = store.get_all_nodes()
        assert len(nodes) == 0

    def test_stale_nodes_marked_offline(self):
        store = MQTTNodeStore(stale_seconds=1)
        store.update_position("!old", 10.0, 20.0, timestamp=int(time.time()) - 10)
        nodes = store.get_all_nodes()
        assert len(nodes) == 1
        assert nodes[0]["is_online"] is False

    def test_node_count(self):
        store = MQTTNodeStore()
        assert store.node_count == 0
        store.update_position("!a", 1.0, 2.0)
        store.update_position("!b", 3.0, 4.0)
        assert store.node_count == 2

    def test_topology_links(self):
        store = MQTTNodeStore()
        store.update_position("!a", 10.0, 20.0)
        store.update_position("!b", 11.0, 21.0)
        store.update_neighbors("!a", [{"node_id": "!b", "snr": 9.5}])
        links = store.get_topology_links()
        assert len(links) == 1
        assert links[0]["source"] == "!a"
        assert links[0]["target"] == "!b"
        assert links[0]["snr"] == 9.5

    def test_topology_links_skip_missing_coords(self):
        store = MQTTNodeStore()
        store.update_position("!a", 10.0, 20.0)
        # !b has no position
        store.update_neighbors("!a", [{"node_id": "!b", "snr": 5.0}])
        links = store.get_topology_links()
        assert len(links) == 0

    def test_get_all_nodes_returns_copies(self):
        store = MQTTNodeStore()
        store.update_position("!a", 10.0, 20.0)
        nodes = store.get_all_nodes()
        nodes[0]["latitude"] = 999
        # Original should be unchanged
        fresh = store.get_all_nodes()
        assert fresh[0]["latitude"] == 10.0

    def test_eviction_when_max_nodes_reached(self):
        store = MQTTNodeStore(max_nodes=3)
        store.update_position("!a", 1.0, 2.0, timestamp=100)
        store.update_position("!b", 3.0, 4.0, timestamp=200)
        store.update_position("!c", 5.0, 6.0, timestamp=300)
        assert store.node_count == 3
        # Adding a 4th should evict the oldest (!a with timestamp=100)
        store.update_position("!d", 7.0, 8.0, timestamp=400)
        assert store.node_count == 3
        nodes = store.get_all_nodes()
        node_ids = {n["id"] for n in nodes}
        assert "!a" not in node_ids
        assert "!d" in node_ids

    def test_cleanup_stale_nodes(self):
        store = MQTTNodeStore(remove_seconds=5)
        now = int(time.time())
        store.update_position("!fresh", 1.0, 2.0, timestamp=now)
        store.update_position("!stale", 3.0, 4.0, timestamp=now - 100)
        removed = store.cleanup_stale_nodes()
        assert removed == 1
        assert store.node_count == 1
        nodes = store.get_all_nodes()
        assert nodes[0]["id"] == "!fresh"

    def test_cleanup_also_removes_neighbor_data(self):
        store = MQTTNodeStore(remove_seconds=5)
        now = int(time.time())
        store.update_position("!stale", 1.0, 2.0, timestamp=now - 100)
        store.update_neighbors("!stale", [{"node_id": "!b", "snr": 5.0}])
        removed = store.cleanup_stale_nodes()
        assert removed == 1
        # Neighbor data should also be cleaned
        links = store.get_topology_links()
        assert len(links) == 0


class TestMQTTSubscriber:
    """Tests for MQTTSubscriber initialization."""

    def test_available_without_paho(self):
        # In test env, paho-mqtt likely not installed
        sub = MQTTSubscriber()
        # available depends on paho-mqtt being installed
        assert isinstance(sub.available, bool)

    def test_store_is_accessible(self):
        sub = MQTTSubscriber()
        assert isinstance(sub.store, MQTTNodeStore)

    def test_start_without_paho_returns_false(self):
        sub = MQTTSubscriber()
        if not sub.available:
            assert sub.start() is False

    def test_stop_is_safe(self):
        sub = MQTTSubscriber()
        sub.stop()  # Should not raise
