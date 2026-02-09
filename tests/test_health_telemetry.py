"""Tests for extended air quality and health telemetry support."""

import unittest

from src.collectors.mqtt_subscriber import MQTTNodeStore


class TestAirQualityTelemetry(unittest.TestCase):
    """Test air quality fields in MQTTNodeStore."""

    def test_update_telemetry_air_quality(self):
        """Store accepts air quality fields via **extra kwargs."""
        store = MQTTNodeStore()
        store.update_telemetry(
            "!aq0001",
            pm25_standard=15,
            pm100_standard=30,
            co2=800,
            pm_voc_idx=120.5,
            pm_nox_idx=50.0,
        )
        nodes = store.get_all_nodes()
        # No position yet, so no nodes returned by get_all_nodes
        # (which filters by valid coordinates)
        # Access internal store directly
        node = store._nodes.get("!aq0001")
        self.assertIsNotNone(node)
        self.assertEqual(node["pm25_standard"], 15)
        self.assertEqual(node["pm100_standard"], 30)
        self.assertEqual(node["co2"], 800)
        self.assertEqual(node["pm_voc_idx"], 120.5)
        self.assertEqual(node["pm_nox_idx"], 50.0)

    def test_update_telemetry_iaq(self):
        """IAQ field stored via named parameter."""
        store = MQTTNodeStore()
        store.update_telemetry("!aq0002", iaq=75)
        node = store._nodes.get("!aq0002")
        self.assertEqual(node["iaq"], 75)

    def test_update_telemetry_environmental_extra(self):
        """Environmental readings alongside IAQ."""
        store = MQTTNodeStore()
        store.update_telemetry(
            "!aq0003",
            temperature=22.5,
            humidity=45.0,
            pressure=1013.25,
            iaq=120,
        )
        node = store._nodes.get("!aq0003")
        self.assertEqual(node["temperature"], 22.5)
        self.assertEqual(node["humidity"], 45.0)
        self.assertEqual(node["pressure"], 1013.25)
        self.assertEqual(node["iaq"], 120)

    def test_air_quality_with_position_in_geojson(self):
        """Node with position and air quality data appears in get_all_nodes."""
        store = MQTTNodeStore()
        store.update_position("!aq0004", 40.0, -105.0)
        store.update_telemetry("!aq0004", pm25_standard=25, co2=1200)
        nodes = store.get_all_nodes()
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["pm25_standard"], 25)
        self.assertEqual(nodes[0]["co2"], 1200)


class TestHealthTelemetry(unittest.TestCase):
    """Test health/biometric fields in MQTTNodeStore."""

    def test_update_telemetry_health(self):
        """Store accepts health fields via **extra kwargs."""
        store = MQTTNodeStore()
        store.update_telemetry(
            "!hm0001",
            heart_bpm=72,
            spo2=98,
            body_temperature=36.6,
        )
        node = store._nodes.get("!hm0001")
        self.assertIsNotNone(node)
        self.assertEqual(node["heart_bpm"], 72)
        self.assertEqual(node["spo2"], 98)
        self.assertEqual(node["body_temperature"], 36.6)

    def test_health_with_position_in_geojson(self):
        """Node with position and health data in get_all_nodes."""
        store = MQTTNodeStore()
        store.update_position("!hm0002", 41.0, -106.0)
        store.update_telemetry("!hm0002", heart_bpm=80, spo2=97)
        nodes = store.get_all_nodes()
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["heart_bpm"], 80)
        self.assertEqual(nodes[0]["spo2"], 97)


class TestNoneValuesSkipped(unittest.TestCase):
    """Test that None extra kwargs are not stored."""

    def test_none_extra_not_stored(self):
        """None values in **extra are skipped."""
        store = MQTTNodeStore()
        store.update_telemetry("!skip01", pm25_standard=None, co2=500)
        node = store._nodes.get("!skip01")
        self.assertNotIn("pm25_standard", node)
        self.assertEqual(node["co2"], 500)

    def test_none_iaq_not_stored(self):
        """None IAQ not stored."""
        store = MQTTNodeStore()
        store.update_telemetry("!skip02", iaq=None, temperature=22.0)
        node = store._nodes.get("!skip02")
        self.assertNotIn("iaq", node)
        self.assertEqual(node["temperature"], 22.0)


class TestCombinedTelemetry(unittest.TestCase):
    """Test nodes with multiple telemetry types."""

    def test_device_and_air_quality_and_health(self):
        """Node with device metrics, air quality, and health."""
        store = MQTTNodeStore()
        store.update_position("!combo1", 42.0, -107.0)
        # Device metrics
        store.update_telemetry("!combo1", battery=85, voltage=4.1)
        # Environment + IAQ
        store.update_telemetry("!combo1", temperature=22.0, iaq=75)
        # Air quality
        store.update_telemetry("!combo1", pm25_standard=10, co2=400)
        # Health
        store.update_telemetry("!combo1", heart_bpm=65, spo2=99)

        nodes = store.get_all_nodes()
        self.assertEqual(len(nodes), 1)
        n = nodes[0]
        self.assertEqual(n["battery"], 85)
        self.assertEqual(n["temperature"], 22.0)
        self.assertEqual(n["iaq"], 75)
        self.assertEqual(n["pm25_standard"], 10)
        self.assertEqual(n["co2"], 400)
        self.assertEqual(n["heart_bpm"], 65)
        self.assertEqual(n["spo2"], 99)


if __name__ == "__main__":
    unittest.main()
