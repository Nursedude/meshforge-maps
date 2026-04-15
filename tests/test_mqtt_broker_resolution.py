"""Tests for multi-broker MQTT config resolution."""

from src.collectors.aggregator import _resolve_broker_specs


class TestResolveBrokerSpecs:
    def test_fallback_to_scalar_keys(self):
        config = {
            "mqtt_broker": "mqtt.example.org",
            "mqtt_port": 1883,
            "mqtt_topic": "msh/US",
            "mqtt_username": "u",
            "mqtt_password": "p",
            "mqtt_use_tls": False,
        }
        specs = _resolve_broker_specs(config)
        assert len(specs) == 1
        assert specs[0]["broker"] == "mqtt.example.org"
        assert specs[0]["topic"] == "msh/US"
        assert specs[0]["username"] == "u"

    def test_multi_broker_list_used_when_populated(self):
        config = {
            "mqtt_broker": "legacy.example.org",  # should be ignored
            "mqtt_brokers": [
                {"broker": "a.example.org", "port": 1883, "topic": "msh/A", "label": "A"},
                {"broker": "b.example.org", "port": 8883, "topic": "msh/B", "use_tls": True},
            ],
        }
        specs = _resolve_broker_specs(config)
        assert [s["broker"] for s in specs] == ["a.example.org", "b.example.org"]
        assert specs[1]["use_tls"] is True

    def test_invalid_entry_skipped(self):
        config = {
            "mqtt_brokers": [
                {"broker": "good.example.org"},
                {"port": 1883},  # no broker key — invalid
                "junk",           # not a dict
            ],
        }
        specs = _resolve_broker_specs(config)
        assert len(specs) == 1
        assert specs[0]["broker"] == "good.example.org"
