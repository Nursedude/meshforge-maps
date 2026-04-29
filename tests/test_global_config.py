"""Tests for the MeshForge ecosystem-wide global config layer (read side)."""

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from src.utils.global_config import (
    GLOBAL_CONFIG_DIRNAME,
    GLOBAL_CONFIG_FILENAME,
    global_config_path,
    load_global_overrides,
)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def test_global_config_path_layout():
    p = global_config_path()
    assert p.name == GLOBAL_CONFIG_FILENAME
    assert p.parent.name == GLOBAL_CONFIG_DIRNAME
    assert p.parent.parent.name == ".config"


# ---------------------------------------------------------------------------
# load_global_overrides — happy / missing / malformed
# ---------------------------------------------------------------------------


def test_missing_file_returns_empty(tmp_path):
    overrides = load_global_overrides(tmp_path / "nope.ini")
    assert overrides == {}


def test_full_config_maps_to_flat_keys(tmp_path):
    target = tmp_path / "global.ini"
    target.write_text(textwrap.dedent("""\
        [mqtt]
        broker = my-broker.local
        port = 8883
        use_tls = true
        username = wh6gxz
        password = secret123
        topic_root = msh/US/HI

        [region]
        preset = hawaii
        home_lat = 21.30
        home_lon = -157.85
    """))
    overrides = load_global_overrides(target)
    assert overrides["mqtt_broker"] == "my-broker.local"
    assert overrides["mqtt_port"] == 8883
    assert overrides["mqtt_use_tls"] is True
    assert overrides["mqtt_username"] == "wh6gxz"
    assert overrides["mqtt_password"] == "secret123"
    # Topic root gets the v2 wildcard appended
    assert overrides["mqtt_topic"] == "msh/US/HI/2/e/#"
    assert overrides["region_preset"] == "hawaii"
    assert overrides["map_center_lat"] == pytest.approx(21.30)
    assert overrides["map_center_lon"] == pytest.approx(-157.85)


def test_topic_root_with_existing_wildcard_passes_through(tmp_path):
    target = tmp_path / "global.ini"
    target.write_text("[mqtt]\ntopic_root = msh/US/2/e/#\n")
    overrides = load_global_overrides(target)
    assert overrides["mqtt_topic"] == "msh/US/2/e/#"


def test_topic_root_with_v2_path_passes_through(tmp_path):
    target = tmp_path / "global.ini"
    target.write_text("[mqtt]\ntopic_root = msh/US/HI/2/e\n")
    overrides = load_global_overrides(target)
    assert overrides["mqtt_topic"] == "msh/US/HI/2/e"


def test_use_tls_blank_omitted(tmp_path):
    """Blank use_tls means 'unset' — don't override the False default."""
    target = tmp_path / "global.ini"
    target.write_text("[mqtt]\nbroker = b.example\nuse_tls =\n")
    overrides = load_global_overrides(target)
    assert overrides["mqtt_broker"] == "b.example"
    assert "mqtt_use_tls" not in overrides


def test_use_tls_explicit_false_propagates(tmp_path):
    """Explicit false IS distinguishable from blank — must override."""
    target = tmp_path / "global.ini"
    target.write_text("[mqtt]\nuse_tls = false\n")
    overrides = load_global_overrides(target)
    assert overrides["mqtt_use_tls"] is False


def test_blank_strings_are_omitted(tmp_path):
    target = tmp_path / "global.ini"
    target.write_text(textwrap.dedent("""\
        [mqtt]
        broker =
        username =
        password =
    """))
    overrides = load_global_overrides(target)
    assert "mqtt_broker" not in overrides
    assert "mqtt_username" not in overrides
    assert "mqtt_password" not in overrides


def test_unknown_preset_passes_through(tmp_path):
    """Unknown presets pass through; maps' validator decides if they're valid."""
    target = tmp_path / "global.ini"
    target.write_text("[region]\npreset = west_coast\n")
    overrides = load_global_overrides(target)
    assert overrides["region_preset"] == "west_coast"


def test_european_preset_omitted(tmp_path):
    """maps doesn't have a europe REGION_PRESET → don't try to set one."""
    target = tmp_path / "global.ini"
    target.write_text("[region]\npreset = europe\n")
    overrides = load_global_overrides(target)
    assert "region_preset" not in overrides


def test_us_alias_maps_to_canonical(tmp_path):
    target = tmp_path / "global.ini"
    target.write_text("[region]\npreset = default_us\n")
    overrides = load_global_overrides(target)
    assert overrides["region_preset"] == "us"


def test_malformed_int_skipped(tmp_path):
    target = tmp_path / "global.ini"
    target.write_text("[mqtt]\nbroker = b.example\nport = not-a-number\n")
    overrides = load_global_overrides(target)
    assert overrides["mqtt_broker"] == "b.example"
    assert "mqtt_port" not in overrides  # zero default → skipped


def test_out_of_range_coords_skipped(tmp_path):
    target = tmp_path / "global.ini"
    target.write_text(textwrap.dedent("""\
        [region]
        home_lat = 999.0
        home_lon = -200.0
    """))
    overrides = load_global_overrides(target)
    assert "map_center_lat" not in overrides
    assert "map_center_lon" not in overrides


def test_garbage_file_does_not_crash(tmp_path):
    target = tmp_path / "global.ini"
    # Duplicate section header → DuplicateSectionError in configparser
    target.write_text("[mqtt]\nbroker = a\n[mqtt]\nbroker = b\n")
    overrides = load_global_overrides(target)
    assert overrides == {}


def test_empty_sections_yield_empty_overrides(tmp_path):
    target = tmp_path / "global.ini"
    target.write_text("[mqtt]\n[region]\n")
    overrides = load_global_overrides(target)
    assert overrides == {}


# ---------------------------------------------------------------------------
# MapsConfig integration — global seeds, settings.json overrides
# ---------------------------------------------------------------------------


def test_mapsconfig_picks_up_global_when_no_settings(tmp_path, monkeypatch):
    """Empty settings.json → global values propagate to MapsConfig."""
    from src.utils import config as config_mod

    target = tmp_path / "global.ini"
    target.write_text(textwrap.dedent("""\
        [mqtt]
        broker = global-broker.example
        port = 8883
    """))

    # Redirect global_config to our tmp file
    monkeypatch.setattr(
        config_mod,
        "load_global_overrides",
        lambda: __import__(
            "src.utils.global_config", fromlist=["load_global_overrides"]
        ).load_global_overrides(target),
    )

    settings = tmp_path / "settings.json"
    cfg = config_mod.MapsConfig(config_path=settings)
    assert cfg.get("mqtt_broker") == "global-broker.example"
    assert cfg.get("mqtt_port") == 8883


def test_mapsconfig_settings_override_global(tmp_path, monkeypatch):
    """settings.json wins over global.ini."""
    import json

    from src.utils import config as config_mod

    target = tmp_path / "global.ini"
    target.write_text("[mqtt]\nbroker = global-broker.example\n")
    monkeypatch.setattr(
        config_mod,
        "load_global_overrides",
        lambda: __import__(
            "src.utils.global_config", fromlist=["load_global_overrides"]
        ).load_global_overrides(target),
    )

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"mqtt_broker": "settings-broker.example"}))

    cfg = config_mod.MapsConfig(config_path=settings)
    assert cfg.get("mqtt_broker") == "settings-broker.example"


def test_mapsconfig_no_global_uses_default_config(tmp_path, monkeypatch):
    """Missing global.ini → behavior identical to pre-global-config."""
    from src.utils import config as config_mod

    monkeypatch.setattr(
        config_mod,
        "load_global_overrides",
        lambda: __import__(
            "src.utils.global_config", fromlist=["load_global_overrides"]
        ).load_global_overrides(tmp_path / "absent.ini"),
    )

    settings = tmp_path / "settings.json"
    cfg = config_mod.MapsConfig(config_path=settings)
    # DEFAULT_CONFIG broker survives unchanged
    assert cfg.get("mqtt_broker") == "mqtt.meshtastic.org"
