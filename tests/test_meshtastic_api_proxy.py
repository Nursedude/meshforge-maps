"""Tests for MeshtasticApiProxy — meshtasticd-compatible JSON API proxy."""

import json
import threading
import time
import unittest
from http.client import HTTPConnection

from src.collectors.mqtt_subscriber import MQTTNodeStore
from src.utils.meshtastic_api_proxy import (
    MeshtasticApiProxy,
    _format_node_meshtastic,
)


class TestFormatNodeMeshtastic(unittest.TestCase):
    """Test the node formatting function."""

    def test_basic_node(self):
        """Format a minimal node dict."""
        node = {
            "id": "!a1b2c3d4",
            "name": "TestNode",
            "latitude": 40.0,
            "longitude": -105.0,
        }
        result = _format_node_meshtastic(node)
        self.assertEqual(result["user"]["id"], "!a1b2c3d4")
        self.assertEqual(result["user"]["longName"], "TestNode")
        self.assertEqual(result["position"]["latitude"], 40.0)
        self.assertEqual(result["position"]["longitude"], -105.0)
        # num should be hex-converted integer
        self.assertEqual(result["num"], 0xa1b2c3d4)

    def test_node_with_device_metrics(self):
        """Format a node with battery and voltage."""
        node = {
            "id": "!11223344",
            "battery": 85,
            "voltage": 4.1,
            "channel_util": 12.5,
            "air_util_tx": 3.2,
        }
        result = _format_node_meshtastic(node)
        dm = result["deviceMetrics"]
        self.assertEqual(dm["batteryLevel"], 85)
        self.assertEqual(dm["voltage"], 4.1)
        self.assertEqual(dm["channelUtilization"], 12.5)
        self.assertEqual(dm["airUtilTx"], 3.2)

    def test_node_with_environment_metrics(self):
        """Format node with temperature, humidity, pressure, IAQ."""
        node = {
            "id": "!aabbccdd",
            "temperature": 22.5,
            "humidity": 45.0,
            "pressure": 1013.25,
            "iaq": 75,
        }
        result = _format_node_meshtastic(node)
        em = result["environmentMetrics"]
        self.assertEqual(em["temperature"], 22.5)
        self.assertEqual(em["relativeHumidity"], 45.0)
        self.assertEqual(em["barometricPressure"], 1013.25)
        self.assertEqual(em["iaq"], 75)

    def test_node_with_air_quality_metrics(self):
        """Format node with PM2.5, CO2, VOC data."""
        node = {
            "id": "!11111111",
            "pm25_standard": 15,
            "co2": 800,
            "pm_voc_idx": 120.5,
        }
        result = _format_node_meshtastic(node)
        aq = result["airQualityMetrics"]
        self.assertEqual(aq["pm25_standard"], 15)
        self.assertEqual(aq["co2"], 800)
        self.assertEqual(aq["pm_voc_idx"], 120.5)

    def test_node_with_health_metrics(self):
        """Format node with heart rate and SpO2."""
        node = {
            "id": "!22222222",
            "heart_bpm": 72,
            "spo2": 98,
            "body_temperature": 36.6,
        }
        result = _format_node_meshtastic(node)
        hm = result["healthMetrics"]
        self.assertEqual(hm["heartBpm"], 72)
        self.assertEqual(hm["spO2"], 98)
        self.assertEqual(hm["temperature"], 36.6)

    def test_node_without_position(self):
        """Node with no lat/lon should not have position key."""
        node = {"id": "!33333333", "name": "NoPos"}
        result = _format_node_meshtastic(node)
        self.assertNotIn("position", result)

    def test_node_without_metrics(self):
        """Node with no metrics should not have metric keys."""
        node = {"id": "!44444444"}
        result = _format_node_meshtastic(node)
        self.assertNotIn("deviceMetrics", result)
        self.assertNotIn("environmentMetrics", result)
        self.assertNotIn("airQualityMetrics", result)
        self.assertNotIn("healthMetrics", result)

    def test_invalid_node_id_hex(self):
        """Non-hex node ID should produce num=0."""
        node = {"id": "!gggggggg"}
        result = _format_node_meshtastic(node)
        self.assertEqual(result["num"], 0)

    def test_node_id_without_bang(self):
        """Node ID without ! prefix."""
        node = {"id": "nodeid123"}
        result = _format_node_meshtastic(node)
        self.assertEqual(result["num"], 0)


class TestMeshtasticApiProxy(unittest.TestCase):
    """Test the proxy server lifecycle."""

    def test_init_defaults(self):
        proxy = MeshtasticApiProxy()
        self.assertFalse(proxy.running)
        self.assertEqual(proxy.port, 0)

    def test_stats_before_start(self):
        proxy = MeshtasticApiProxy()
        stats = proxy.stats
        self.assertFalse(stats["running"])
        self.assertEqual(stats["request_count"], 0)

    def test_set_store(self):
        store = MQTTNodeStore()
        proxy = MeshtasticApiProxy()
        proxy.set_store(store)
        self.assertIs(proxy._mqtt_store, store)

    def test_start_and_stop(self):
        """Proxy can start and stop without errors."""
        store = MQTTNodeStore()
        proxy = MeshtasticApiProxy(mqtt_store=store, port=19400)
        try:
            self.assertTrue(proxy.start())
            self.assertTrue(proxy.running)
            self.assertGreater(proxy.port, 0)
        finally:
            proxy.stop()
        self.assertFalse(proxy.running)

    def test_double_start(self):
        """Starting twice returns True without error."""
        proxy = MeshtasticApiProxy(port=19401)
        try:
            self.assertTrue(proxy.start())
            self.assertTrue(proxy.start())  # Idempotent
        finally:
            proxy.stop()

    def test_nodes_endpoint(self):
        """GET /api/v1/nodes returns node data from store."""
        store = MQTTNodeStore()
        store.update_position("!aabb0001", 40.0, -105.0)
        store.update_nodeinfo("!aabb0001", long_name="TestNode")
        proxy = MeshtasticApiProxy(mqtt_store=store, port=19402)
        try:
            proxy.start()
            time.sleep(0.1)  # Let server bind

            conn = HTTPConnection("127.0.0.1", proxy.port, timeout=5)
            conn.request("GET", "/api/v1/nodes")
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())
            conn.close()

            self.assertEqual(resp.status, 200)
            self.assertEqual(data["node_count"], 1)
            self.assertEqual(data["nodes"][0]["user"]["longName"], "TestNode")
        finally:
            proxy.stop()

    def test_single_node_endpoint(self):
        """GET /api/v1/nodes/<id> returns a single node."""
        store = MQTTNodeStore()
        store.update_position("!aabb0002", 41.0, -106.0)
        proxy = MeshtasticApiProxy(mqtt_store=store, port=19403)
        try:
            proxy.start()
            time.sleep(0.1)

            conn = HTTPConnection("127.0.0.1", proxy.port, timeout=5)
            conn.request("GET", "/api/v1/nodes/!aabb0002")
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())
            conn.close()

            self.assertEqual(resp.status, 200)
            self.assertEqual(data["user"]["id"], "!aabb0002")
        finally:
            proxy.stop()

    def test_node_not_found(self):
        """GET /api/v1/nodes/<id> returns 404 for unknown node."""
        store = MQTTNodeStore()
        proxy = MeshtasticApiProxy(mqtt_store=store, port=19404)
        try:
            proxy.start()
            time.sleep(0.1)

            conn = HTTPConnection("127.0.0.1", proxy.port, timeout=5)
            conn.request("GET", "/api/v1/nodes/!00000000")
            resp = conn.getresponse()
            resp.read()
            conn.close()

            self.assertEqual(resp.status, 404)
        finally:
            proxy.stop()

    def test_topology_endpoint(self):
        """GET /api/v1/topology returns link data."""
        store = MQTTNodeStore()
        store.update_position("!aa000001", 40.0, -105.0)
        store.update_position("!aa000002", 41.0, -106.0)
        store.update_neighbors("!aa000001", [{"node_id": "!aa000002", "snr": 8.5}])
        proxy = MeshtasticApiProxy(mqtt_store=store, port=19405)
        try:
            proxy.start()
            time.sleep(0.1)

            conn = HTTPConnection("127.0.0.1", proxy.port, timeout=5)
            conn.request("GET", "/api/v1/topology")
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())
            conn.close()

            self.assertEqual(resp.status, 200)
            self.assertEqual(data["link_count"], 1)
        finally:
            proxy.stop()

    def test_stats_endpoint(self):
        """GET /api/v1/stats returns proxy statistics."""
        store = MQTTNodeStore()
        proxy = MeshtasticApiProxy(mqtt_store=store, port=19406)
        try:
            proxy.start()
            time.sleep(0.1)

            conn = HTTPConnection("127.0.0.1", proxy.port, timeout=5)
            conn.request("GET", "/api/v1/stats")
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())
            conn.close()

            self.assertEqual(resp.status, 200)
            self.assertTrue(data["proxy_running"])
            self.assertTrue(data["store_available"])
        finally:
            proxy.stop()

    def test_not_found_route(self):
        """GET /api/unknown returns 404."""
        proxy = MeshtasticApiProxy(port=19407)
        try:
            proxy.start()
            time.sleep(0.1)

            conn = HTTPConnection("127.0.0.1", proxy.port, timeout=5)
            conn.request("GET", "/api/unknown")
            resp = conn.getresponse()
            resp.read()
            conn.close()

            self.assertEqual(resp.status, 404)
        finally:
            proxy.stop()

    def test_no_store(self):
        """Proxy without store returns empty node list."""
        proxy = MeshtasticApiProxy(mqtt_store=None, port=19408)
        try:
            proxy.start()
            time.sleep(0.1)

            conn = HTTPConnection("127.0.0.1", proxy.port, timeout=5)
            conn.request("GET", "/api/v1/nodes")
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())
            conn.close()

            self.assertEqual(data["node_count"], 0)
        finally:
            proxy.stop()


class TestProxyResponseHeaders(unittest.TestCase):
    """Test HTTP response header improvements."""

    def test_content_length_header(self):
        """JSON responses include Content-Length header."""
        store = MQTTNodeStore()
        proxy = MeshtasticApiProxy(mqtt_store=store, port=19409)
        try:
            proxy.start()
            time.sleep(0.1)

            conn = HTTPConnection("127.0.0.1", proxy.port, timeout=5)
            conn.request("GET", "/api/v1/nodes")
            resp = conn.getresponse()
            body = resp.read()
            conn.close()

            content_length = resp.getheader("Content-Length")
            self.assertIsNotNone(content_length)
            self.assertEqual(int(content_length), len(body))
        finally:
            proxy.stop()

    def test_server_header_not_python(self):
        """Server header should not leak Python version."""
        store = MQTTNodeStore()
        proxy = MeshtasticApiProxy(mqtt_store=store, port=19410)
        try:
            proxy.start()
            time.sleep(0.1)

            conn = HTTPConnection("127.0.0.1", proxy.port, timeout=5)
            conn.request("GET", "/api/v1/stats")
            resp = conn.getresponse()
            resp.read()
            conn.close()

            server = resp.getheader("Server")
            self.assertNotIn("Python", server)
            self.assertIn("MeshForge", server)
        finally:
            proxy.stop()

    def test_single_node_via_get_node(self):
        """Direct node lookup via get_node() for O(1) performance."""
        store = MQTTNodeStore()
        store.update_position("!ff001122", 42.0, -107.0)
        store.update_nodeinfo("!ff001122", long_name="LookupTest")
        proxy = MeshtasticApiProxy(mqtt_store=store, port=19411)
        try:
            proxy.start()
            time.sleep(0.1)

            conn = HTTPConnection("127.0.0.1", proxy.port, timeout=5)
            conn.request("GET", "/api/v1/nodes/!ff001122")
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())
            conn.close()

            self.assertEqual(resp.status, 200)
            self.assertEqual(data["user"]["id"], "!ff001122")
            self.assertEqual(data["user"]["longName"], "LookupTest")
        finally:
            proxy.stop()

    def test_single_node_lookup_without_prefix(self):
        """Node lookup without ! prefix matches stored node with prefix."""
        store = MQTTNodeStore()
        store.update_position("!aabb3344", 43.0, -108.0)
        proxy = MeshtasticApiProxy(mqtt_store=store, port=19412)
        try:
            proxy.start()
            time.sleep(0.1)

            conn = HTTPConnection("127.0.0.1", proxy.port, timeout=5)
            conn.request("GET", "/api/v1/nodes/aabb3344")
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())
            conn.close()

            self.assertEqual(resp.status, 200)
            self.assertEqual(data["user"]["id"], "!aabb3344")
        finally:
            proxy.stop()


if __name__ == "__main__":
    unittest.main()
