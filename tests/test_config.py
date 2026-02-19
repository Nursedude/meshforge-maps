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


