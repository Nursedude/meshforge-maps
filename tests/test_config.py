"""Tests for MapsConfig configuration management."""

import json
import pytest

from src.utils.config import (
    DEFAULT_CONFIG,
    NETWORK_COLORS,
    TILE_PROVIDERS,
    MapsConfig,
)


class TestMapsConfigDefaults:
    """Tests for default configuration values."""

    def test_defaults_loaded(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        d = config.to_dict()
        for key, value in DEFAULT_CONFIG.items():
            assert d[key] == value


class TestMapsConfigPersistence:
    """Tests for loading and saving settings."""

    def test_save_and_load(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        config.set("http_port", 9999)
        config.save()

        # Load again
        config2 = MapsConfig(config_path=tmp_config)
        assert config2.get("http_port") == 9999

    def test_load_partial_config(self, tmp_config):
        # Write a partial config (only some keys)
        tmp_config.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_config, "w") as f:
            json.dump({"http_port": 7777}, f)

        config = MapsConfig(config_path=tmp_config)
        assert config.get("http_port") == 7777
        # Other defaults should still be present
        assert config.get("enable_meshtastic") is True
        assert config.get("default_tile_provider") == "carto_dark"

    def test_load_invalid_json(self, tmp_config):
        tmp_config.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_config, "w") as f:
            f.write("not json {{{")

        config = MapsConfig(config_path=tmp_config)
        # Should fall back to defaults
        assert config.get("http_port") == 8808

    def test_ignores_unknown_keys(self, tmp_config):
        tmp_config.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_config, "w") as f:
            json.dump({"unknown_key": "value", "http_port": 1234}, f)

        config = MapsConfig(config_path=tmp_config)
        assert config.get("http_port") == 1234
        assert config.get("unknown_key") is None


class TestMapsConfigGetSet:
    """Tests for get/set/update operations."""

    def test_set_valid_key(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        config.set("http_port", 5555)
        assert config.get("http_port") == 5555

    def test_set_unknown_key_ignored(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        config.set("totally_unknown", "value")
        assert config.get("totally_unknown") is None

    def test_get_with_default(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        assert config.get("nonexistent", "fallback") == "fallback"

    def test_update_multiple(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        config.update({"http_port": 4444, "map_default_zoom": 8})
        assert config.get("http_port") == 4444
        assert config.get("map_default_zoom") == 8

    def test_to_dict_is_copy(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        d = config.to_dict()
        d["http_port"] = 9999
        assert config.get("http_port") == 8808  # Original unchanged


class TestMapsConfigSources:
    """Tests for get_enabled_sources()."""

    def test_all_enabled(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        sources = config.get_enabled_sources()
        assert "meshtastic" in sources
        assert "reticulum" in sources
        assert "hamclock" in sources
        assert "aredn" in sources

    def test_disable_one(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        config.set("enable_meshtastic", False)
        sources = config.get_enabled_sources()
        assert "meshtastic" not in sources
        assert "reticulum" in sources

    def test_disable_all(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        config.set("enable_meshtastic", False)
        config.set("enable_reticulum", False)
        config.set("enable_hamclock", False)
        config.set("enable_aredn", False)
        config.set("enable_meshcore", False)
        config.set("enable_noaa_alerts", False)
        assert config.get_enabled_sources() == []


class TestHostConfig:
    """Tests for http_host and ws_host configuration."""

    def test_default_hosts(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        assert config.get("http_host") == "127.0.0.1"
        assert config.get("ws_host") == "127.0.0.1"

    def test_set_host_to_all_interfaces(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        config.update({"http_host": "0.0.0.0", "ws_host": "0.0.0.0"})
        assert config.get("http_host") == "0.0.0.0"
        assert config.get("ws_host") == "0.0.0.0"

    def test_host_persists(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        config.update({"http_host": "0.0.0.0"})
        config.save()
        config2 = MapsConfig(config_path=tmp_config)
        assert config2.get("http_host") == "0.0.0.0"


class TestNoRadioProfile:
    """Tests for no-radio (headless monitor) configuration."""

    def test_no_radio_profile(self, tmp_config):
        """Simulate no-radio config: only MQTT + HamClock enabled."""
        tmp_config.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_config, "w") as f:
            json.dump({
                "enable_meshtastic": True,
                "enable_reticulum": False,
                "enable_hamclock": True,
                "enable_aredn": False,
            }, f)

        config = MapsConfig(config_path=tmp_config)
        sources = config.get_enabled_sources()
        assert "meshtastic" in sources
        assert "hamclock" in sources
        assert "reticulum" not in sources
        assert "aredn" not in sources

    def test_no_radio_with_host_binding(self, tmp_config):
        """No-radio config typically binds to 0.0.0.0 for network access."""
        tmp_config.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_config, "w") as f:
            json.dump({
                "enable_reticulum": False,
                "enable_aredn": False,
                "http_host": "0.0.0.0",
                "ws_host": "0.0.0.0",
            }, f)

        config = MapsConfig(config_path=tmp_config)
        assert config.get("http_host") == "0.0.0.0"
        assert config.get("enable_reticulum") is False
        assert config.get("enable_aredn") is False
        # Defaults preserved for unset keys
        assert config.get("enable_meshtastic") is True
        assert config.get("enable_hamclock") is True


class TestLiteModeOverrides:
    """Lite deployment profile must tighten resource-heavy knobs."""

    def test_lite_tightens_history_retention(self, tmp_config):
        # Lite caps retention at 1 day even if user configured more
        config = MapsConfig(config_path=tmp_config)
        config.set("deployment_profile", "lite")
        config.set("node_history_retention_days", 7)
        assert config.get_effective("node_history_retention_days") == 1

    def test_lite_raises_history_throttle(self, tmp_config):
        # Lite forces at least 600s between observations per node
        config = MapsConfig(config_path=tmp_config)
        config.set("deployment_profile", "lite")
        config.set("node_history_throttle_seconds", 60)
        assert config.get_effective("node_history_throttle_seconds") == 600

    def test_full_profile_preserves_retention(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        config.set("deployment_profile", "full")
        config.set("node_history_retention_days", 7)
        assert config.get_effective("node_history_retention_days") == 7


class TestTileProviders:
    """Tests for tile provider definitions."""

    def test_providers_exist(self, tmp_config):
        config = MapsConfig(config_path=tmp_config)
        providers = config.get_tile_providers()
        assert "carto_dark" in providers
        assert "osm_standard" in providers
        assert "esri_satellite" in providers

    def test_provider_has_required_fields(self):
        for key, provider in TILE_PROVIDERS.items():
            assert "name" in provider, f"{key} missing name"
            assert "url" in provider, f"{key} missing url"
            assert "attribution" in provider, f"{key} missing attribution"
            assert "max_zoom" in provider, f"{key} missing max_zoom"

    def test_provider_urls_are_templates(self):
        for key, provider in TILE_PROVIDERS.items():
            url = provider["url"]
            assert "{z}" in url, f"{key} url missing {{z}}"
            assert "{x}" in url, f"{key} url missing {{x}}"
            assert "{y}" in url, f"{key} url missing {{y}}"


class TestConfigKeyPersistence:
    """Tests that all config keys used in map_server.py exist in DEFAULT_CONFIG."""

    def test_api_key_in_defaults(self):
        assert "api_key" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["api_key"] is None

    def test_meshtastic_proxy_port_in_defaults(self):
        assert "meshtastic_proxy_port" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["meshtastic_proxy_port"] == 4404

    def test_api_key_round_trips(self, tmp_config):
        cfg = MapsConfig(config_path=tmp_config)
        cfg.set("api_key", "test-secret-key")
        cfg.save()
        cfg2 = MapsConfig(config_path=tmp_config)
        assert cfg2.get("api_key") == "test-secret-key"

    def test_proxy_port_round_trips(self, tmp_config):
        cfg = MapsConfig(config_path=tmp_config)
        cfg.set("meshtastic_proxy_port", 5500)
        cfg.save()
        cfg2 = MapsConfig(config_path=tmp_config)
        assert cfg2.get("meshtastic_proxy_port") == 5500


class TestAtomicConfigWrite:
    """Tests for atomic config file writing (Step 10)."""

    def test_save_creates_backup(self, tmp_config):
        """Saving an existing config creates a .bak file."""
        cfg = MapsConfig(config_path=tmp_config)
        cfg.set("http_port", 9999)
        cfg.save()  # First save creates the file
        cfg.set("http_port", 8888)
        cfg.save()  # Second save should create .bak

        bak_path = tmp_config.with_suffix(".json.bak")
        assert bak_path.exists()

        # .bak should contain the old value (9999)
        import json
        with open(bak_path) as f:
            bak_data = json.load(f)
        assert bak_data["http_port"] == 9999

    def test_atomic_write_produces_valid_json(self, tmp_config):
        """After atomic write, the config file is valid JSON."""
        cfg = MapsConfig(config_path=tmp_config)
        cfg.set("http_port", 7777)
        cfg.save()

        import json
        with open(tmp_config) as f:
            data = json.load(f)
        assert data["http_port"] == 7777

    def test_no_temp_files_left_on_success(self, tmp_config):
        """Successful save leaves no temp files behind."""
        cfg = MapsConfig(config_path=tmp_config)
        cfg.save()

        parent = tmp_config.parent
        temp_files = list(parent.glob(".settings_*.tmp"))
        assert len(temp_files) == 0

    def test_original_intact_if_no_first_save(self, tmp_config):
        """First save with no existing file does not create .bak."""
        cfg = MapsConfig(config_path=tmp_config)
        cfg.save()

        bak_path = tmp_config.with_suffix(".json.bak")
        assert not bak_path.exists()


class TestDeploymentProfiles:
    """Tests for lite/medium/full deployment profile overrides."""

    def test_full_profile_no_overrides(self, tmp_config):
        cfg = MapsConfig(config_path=tmp_config)
        cfg.set("deployment_profile", "full")
        cfg.set("cache_ttl_minutes", 15)
        assert cfg.is_lite is False
        assert cfg.is_medium is False
        assert cfg.get_effective("cache_ttl_minutes") == 15
        assert cfg.get_effective("enable_analytics", True) is True

    def test_lite_profile_overrides(self, tmp_config):
        cfg = MapsConfig(config_path=tmp_config)
        cfg.set("deployment_profile", "lite")
        cfg.set("cache_ttl_minutes", 15)  # should be raised to 60
        assert cfg.is_lite is True
        assert cfg.is_medium is False
        assert cfg.get_effective("cache_ttl_minutes") == 60
        assert cfg.get_effective("node_history_throttle_seconds", 0) == 600
        assert cfg.get_effective("node_history_retention_days", 10) == 1
        assert cfg.get_effective("enable_analytics", True) is False
        assert cfg.get_effective("enable_node_state", True) is False
        assert cfg.get_effective("enable_config_drift", True) is False

    def test_medium_profile_overrides(self, tmp_config):
        cfg = MapsConfig(config_path=tmp_config)
        cfg.set("deployment_profile", "medium")
        cfg.set("cache_ttl_minutes", 15)  # should be raised to 30
        assert cfg.is_medium is True
        assert cfg.is_lite is False
        assert cfg.get_effective("cache_ttl_minutes") == 30
        assert cfg.get_effective("node_history_throttle_seconds", 100) == 300
        assert cfg.get_effective("node_history_retention_days", 10) == 2
        # Medium keeps heavy features enabled
        assert cfg.get_effective("enable_analytics", True) is True
        assert cfg.get_effective("enable_node_state", True) is True
        assert cfg.get_effective("enable_config_drift", True) is True

    def test_medium_does_not_lower_already_long_cache(self, tmp_config):
        cfg = MapsConfig(config_path=tmp_config)
        cfg.set("deployment_profile", "medium")
        cfg.set("cache_ttl_minutes", 90)
        assert cfg.get_effective("cache_ttl_minutes") == 90


class TestMqttStoreCap:
    """Tests for the tiered MQTT store capacity helper."""

    def test_cap_by_profile(self, tmp_config):
        from src.collectors.aggregator import _mqtt_store_cap
        cfg = MapsConfig(config_path=tmp_config)

        cfg.set("deployment_profile", "lite")
        assert _mqtt_store_cap(cfg) == 1000

        cfg.set("deployment_profile", "medium")
        assert _mqtt_store_cap(cfg) == 5000

        cfg.set("deployment_profile", "full")
        assert _mqtt_store_cap(cfg) == 10000

    def test_cap_default_for_unknown_profile(self, tmp_config):
        from src.collectors.aggregator import _mqtt_store_cap
        cfg = MapsConfig(config_path=tmp_config)
        cfg.set("deployment_profile", "something-else")
        assert _mqtt_store_cap(cfg) == 10000
