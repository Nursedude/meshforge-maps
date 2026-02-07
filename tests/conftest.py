"""Shared fixtures for meshforge-maps test suite."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock


@pytest.fixture
def tmp_config(tmp_path):
    """Provide a temporary config file path."""
    return tmp_path / "settings.json"


@pytest.fixture
def sample_meshtastic_api_node():
    """Meshtastic node as returned by meshtasticd HTTP API."""
    return {
        "num": 1234567890,
        "user": {
            "id": "!a1b2c3d4",
            "longName": "TestNode-Alpha",
            "shortName": "TNA",
            "hwModel": "TBEAM",
            "role": "CLIENT",
        },
        "position": {
            "latitude": 35.6895,
            "longitude": 139.6917,
            "altitude": 40,
        },
        "snr": 9.5,
        "lastHeard": 1700000000,
        "deviceMetrics": {
            "batteryLevel": 87,
        },
    }


@pytest.fixture
def sample_meshtastic_api_node_integer_coords():
    """Meshtastic node with latitudeI/longitudeI (integer * 1e7)."""
    return {
        "num": 9876543210,
        "user": {
            "id": "!f0e1d2c3",
            "longName": "IntegerCoords-Node",
            "shortName": "ICN",
            "hwModel": "HELTEC_V3",
        },
        "position": {
            "latitudeI": 406892532,   # ~40.6892532
            "longitudeI": -740466305,  # ~-74.0466305
            "altitude": 93,
        },
        "lastHeard": 1700000000,
    }


@pytest.fixture
def sample_mqtt_cache_dict():
    """MQTT cache in dict-of-nodes format."""
    return {
        "!mqtt001": {
            "name": "MQTT-Node-1",
            "latitude": 51.5074,
            "longitude": -0.1278,
            "hardware": "TBEAM",
            "role": "ROUTER",
            "battery": 95,
            "snr": 12.3,
            "is_online": True,
            "last_seen": 1700000000,
        },
        "!mqtt002": {
            "name": "MQTT-Node-2",
            "latitude": 48.8566,
            "longitude": 2.3522,
            "hardware": "HELTEC_V3",
        },
    }


@pytest.fixture
def sample_mqtt_cache_geojson():
    """MQTT cache in GeoJSON FeatureCollection format."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-122.4194, 37.7749]},
                "properties": {
                    "id": "!geo001",
                    "name": "GeoJSON-Node",
                    "network": "meshtastic",
                },
            }
        ],
    }


@pytest.fixture
def sample_rns_interface():
    """RNS interface entry from rnstatus --json."""
    return {
        "name": "RNode-LoRa-900",
        "type": "rnode",
        "hash": "abc123def456",
        "latitude": 34.0522,
        "longitude": -118.2437,
        "status": "up",
        "description": "900MHz LoRa RNode",
        "height": 150,
    }


@pytest.fixture
def sample_aredn_sysinfo():
    """AREDN sysinfo.json response."""
    return {
        "node": "KN6PLV-HAP",
        "lat": "34.0522",
        "lon": "-118.2437",
        "model": "MikroTik hAP ac lite",
        "firmware_version": "3.24.4.0",
        "api_version": "1.15",
        "grid_square": "DM04",
        "sysinfo": {
            "uptime": "3 days, 14:22:01",
            "loads": [0.12, 0.08, 0.05],
        },
        "lqm": [
            {"name": "KN6PLV-SECTOR1", "snr": 25, "quality": 100},
            {"name": "AB1CDE-OMNI", "snr": 18, "quality": 85},
        ],
    }


@pytest.fixture
def sample_geojson_feature_collection():
    """A complete GeoJSON FeatureCollection from the aggregator."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [139.6917, 35.6895]},
                "properties": {
                    "id": "!a1b2c3d4",
                    "name": "TestNode-Alpha",
                    "network": "meshtastic",
                    "node_type": "meshtastic_node",
                    "hardware": "TBEAM",
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-118.2437, 34.0522]},
                "properties": {
                    "id": "abc123def456",
                    "name": "RNode-LoRa-900",
                    "network": "reticulum",
                    "node_type": "RNode (LoRa)",
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-118.2437, 34.0522]},
                "properties": {
                    "id": "KN6PLV-HAP",
                    "name": "KN6PLV-HAP",
                    "network": "aredn",
                    "node_type": "aredn_node",
                },
            },
        ],
        "properties": {
            "source": "aggregated",
            "node_count": 3,
        },
    }
