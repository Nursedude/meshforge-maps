"""Tests for the aredn_node_ips / aredn_node_targets compat layer.

Phase B of the map-domain audit arc renames the AREDN auto-discovery
key in meshforge-maps from `aredn_node_targets` to `aredn_node_ips` so
it matches what meshforge core writes in
~/.config/meshforge/map_settings.json. A one-cycle compat layer keeps
existing fleet boxes working until the rename audit completes.

Contract:
- New key set, legacy unset:    aredn_node_ips read; no warning.
- Legacy set, new unset:        legacy copied into aredn_node_ips;
                                deprecation warning logged.
- Both set:                     new key wins; no warning (operator
                                already migrated, legacy is residual).
- Neither set:                  default list used; no warning.
"""

import json
import logging

from src.utils.config import MapsConfig


class TestArednNodeIpsCompat:
    """Phase B compat — aredn_node_targets → aredn_node_ips."""

    DEFAULT_IPS = ["localnode.local.mesh", "10.0.0.1", "localnode"]

    def _write(self, tmp_config, payload):
        tmp_config.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_config, "w") as f:
            json.dump(payload, f)

    def test_canonical_key_only(self, tmp_config, caplog):
        """aredn_node_ips alone — used as-is, no deprecation warning."""
        self._write(tmp_config, {"aredn_node_ips": ["10.54.25.1"]})
        with caplog.at_level(logging.WARNING):
            cfg = MapsConfig(config_path=tmp_config)
        assert cfg.get("aredn_node_ips") == ["10.54.25.1"]
        assert not any("deprecated" in r.message for r in caplog.records)

    def test_legacy_key_only_copies_and_warns(self, tmp_config, caplog):
        """aredn_node_targets alone — copied to aredn_node_ips + warns."""
        self._write(tmp_config, {"aredn_node_targets": ["10.99.0.1"]})
        with caplog.at_level(logging.WARNING):
            cfg = MapsConfig(config_path=tmp_config)
        assert cfg.get("aredn_node_ips") == ["10.99.0.1"]
        assert cfg.get("aredn_node_targets") == ["10.99.0.1"]
        deprecation = [r for r in caplog.records if "deprecated" in r.message]
        assert len(deprecation) == 1
        assert "aredn_node_targets" in deprecation[0].message
        assert "aredn_node_ips" in deprecation[0].message

    def test_both_keys_canonical_wins(self, tmp_config, caplog):
        """Both keys present — canonical wins, no warning (already migrated)."""
        self._write(
            tmp_config,
            {
                "aredn_node_ips": ["10.1.1.1"],
                "aredn_node_targets": ["10.99.0.1"],
            },
        )
        with caplog.at_level(logging.WARNING):
            cfg = MapsConfig(config_path=tmp_config)
        assert cfg.get("aredn_node_ips") == ["10.1.1.1"]
        # Legacy still readable but the running process uses the canonical key
        assert cfg.get("aredn_node_targets") == ["10.99.0.1"]
        assert not any("deprecated" in r.message for r in caplog.records)

    def test_neither_key_uses_default(self, tmp_config, caplog):
        """No AREDN config at all — default list, no warning."""
        self._write(tmp_config, {"http_port": 8808})
        with caplog.at_level(logging.WARNING):
            cfg = MapsConfig(config_path=tmp_config)
        assert cfg.get("aredn_node_ips") == self.DEFAULT_IPS
        assert cfg.get("aredn_node_targets") is None
        assert not any("deprecated" in r.message for r in caplog.records)

    def test_no_file_uses_default(self, tmp_config, caplog):
        """No config file — defaults, no warning."""
        # tmp_config points at a path that doesn't exist
        with caplog.at_level(logging.WARNING):
            cfg = MapsConfig(config_path=tmp_config)
        assert cfg.get("aredn_node_ips") == self.DEFAULT_IPS
        assert cfg.get("aredn_node_targets") is None
        assert not any("deprecated" in r.message for r in caplog.records)

    def test_legacy_empty_list_still_canonical(self, tmp_config, caplog):
        """aredn_node_targets explicitly set to [] — warn, leave canonical default.

        An empty list is the operator's way of saying "no AREDN nodes
        configured." We still warn (to push toward rename) but the
        canonical default list stays — matching the "neither set" case.
        Future explicit aredn_node_ips=[] will correctly suppress probes.
        """
        self._write(tmp_config, {"aredn_node_targets": []})
        with caplog.at_level(logging.WARNING):
            cfg = MapsConfig(config_path=tmp_config)
        # Legacy was set (so we warn) but its value was empty so we
        # don't clobber the canonical default with [].
        assert cfg.get("aredn_node_ips") == self.DEFAULT_IPS
        assert cfg.get("aredn_node_targets") == []
        deprecation = [r for r in caplog.records if "deprecated" in r.message]
        assert len(deprecation) == 1


class TestArednConfigDefaults:
    """Lock the canonical default list shape so renames don't drift."""

    def test_canonical_default_present(self):
        from src.utils.config import DEFAULT_CONFIG

        assert "aredn_node_ips" in DEFAULT_CONFIG
        assert isinstance(DEFAULT_CONFIG["aredn_node_ips"], list)
        # Default seeds the typical AREDN local-node hostnames
        assert "localnode.local.mesh" in DEFAULT_CONFIG["aredn_node_ips"]

    def test_legacy_default_is_none(self):
        """Legacy key must default to None so unset operator configs
        don't flip the load-path into the deprecation branch."""
        from src.utils.config import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["aredn_node_targets"] is None
