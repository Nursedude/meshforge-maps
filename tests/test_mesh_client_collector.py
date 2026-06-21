"""Tests for MeshClientCollector — file-based GeoJSON ingest (issue #78)."""

import json
import time
from datetime import datetime, timedelta, timezone

from src.collectors.base import ONLINE_THRESHOLDS
from src.collectors.mesh_client_collector import MeshClientCollector
from src.utils.config import DEFAULT_CONFIG


def _writer_feature(node_id="!a1b2c3d4", lon=-122.4194, lat=37.7749,
                    last_heard=None, **props):
    """Build one feature in the writer's (meshing_around) shape."""
    if last_heard is None:
        # Match the real writer (get_geojson): last_heard is an ISO-8601 string.
        last_heard = datetime.now(timezone.utc).isoformat()
    p = {
        "node_id": node_id,
        "name": "Node A",
        "short_name": "NA",
        "long_name": "Node Alpha",
        "hardware_model": "TBEAM",
        "is_online": True,
        "last_heard": last_heard,
        "altitude": 12,
    }
    p.update(props)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": p,
    }


def _snapshot(features, updated="2026-06-20T00:00:00Z"):
    fc = {"type": "FeatureCollection", "features": features}
    if updated is not None:
        fc["updated"] = updated
    return fc


def _write(path, obj):
    path.write_text(json.dumps(obj))
    return str(path)


class TestMeshClientCollectorHappyPath:
    def test_valid_file_yields_tagged_feature(self, tmp_path):
        f = tmp_path / "nodes.geojson"
        _write(f, _snapshot([_writer_feature()]))
        fc = MeshClientCollector(path=str(f))._fetch()

        assert fc["type"] == "FeatureCollection"
        assert len(fc["features"]) == 1
        props = fc["features"][0]["properties"]
        # dedup key against MeshtasticCollector is properties.id == node_id
        assert props["id"] == "!a1b2c3d4"
        assert props["network"] == "meshtastic"
        assert props["source"] == "mesh_client"
        assert props["name"] == "Node Alpha"  # long_name preferred
        # GeoJSON order preserved: [lon, lat]
        assert fc["features"][0]["geometry"]["coordinates"] == [-122.4194, 37.7749]

    def test_collect_caches_and_reports_source(self, tmp_path):
        f = tmp_path / "nodes.geojson"
        _write(f, _snapshot([_writer_feature(), _writer_feature(node_id="!b2c3d4e5")]))
        c = MeshClientCollector(path=str(f), cache_ttl_seconds=600)
        fc = c.collect()
        assert fc["properties"]["source"] == "mesh_client"
        assert fc["properties"]["node_count"] == 2

    def test_extra_props_carried_through(self, tmp_path):
        f = tmp_path / "nodes.geojson"
        _write(f, _snapshot([_writer_feature(
            battery_level=88, snr=5.5, channel_utilization=12.0, quality_percent=90,
        )]))
        props = MeshClientCollector(path=str(f))._fetch()["features"][0]["properties"]
        assert props["battery"] == 88          # battery_level -> battery
        assert props["snr"] == 5.5
        assert props["channel_utilization"] == 12.0
        assert props["quality_percent"] == 90


class TestMeshClientCollectorDegradesToEmpty:
    def test_missing_file_empty_no_exception(self, tmp_path):
        fc = MeshClientCollector(path=str(tmp_path / "nope.geojson"))._fetch()
        assert fc["type"] == "FeatureCollection"
        assert fc["features"] == []

    def test_empty_path_empty_no_exception(self, tmp_path):
        fc = MeshClientCollector(path="")._fetch()
        assert fc["features"] == []

    def test_bad_json_empty_no_exception(self, tmp_path):
        f = tmp_path / "nodes.geojson"
        f.write_text("{not valid json")
        fc = MeshClientCollector(path=str(f))._fetch()
        assert fc["features"] == []

    def test_wrong_type_empty(self, tmp_path):
        f = tmp_path / "nodes.geojson"
        _write(f, {"type": "Feature", "features": [_writer_feature()]})
        fc = MeshClientCollector(path=str(f))._fetch()
        assert fc["features"] == []

    def test_oversize_file_refused(self, tmp_path):
        f = tmp_path / "nodes.geojson"
        _write(f, _snapshot([_writer_feature()]))
        # Tiny cap so a normal file trips the guard without a huge fixture.
        fc = MeshClientCollector(path=str(f), max_bytes=10)._fetch()
        assert fc["features"] == []

    def test_features_not_a_list(self, tmp_path):
        f = tmp_path / "nodes.geojson"
        _write(f, {"type": "FeatureCollection", "features": "oops"})
        fc = MeshClientCollector(path=str(f))._fetch()
        assert fc["features"] == []


class TestMeshClientCollectorValidation:
    def test_invalid_node_id_skipped(self, tmp_path):
        f = tmp_path / "nodes.geojson"
        _write(f, _snapshot([
            _writer_feature(node_id="not-a-hex-id!!"),
            _writer_feature(node_id="!cafe1234"),
        ]))
        fc = MeshClientCollector(path=str(f))._fetch()
        ids = [x["properties"]["id"] for x in fc["features"]]
        assert ids == ["!cafe1234"]

    def test_null_island_and_out_of_range_skipped(self, tmp_path):
        f = tmp_path / "nodes.geojson"
        _write(f, _snapshot([
            _writer_feature(node_id="!00000001", lon=0.0, lat=0.0),       # Null Island
            _writer_feature(node_id="!00000002", lon=999.0, lat=10.0),    # out of range
            _writer_feature(node_id="!00000003", lon=-122.0, lat=37.0),   # valid
        ]))
        fc = MeshClientCollector(path=str(f))._fetch()
        ids = [x["properties"]["id"] for x in fc["features"]]
        assert ids == ["!00000003"]

    def test_malformed_features_skipped(self, tmp_path):
        f = tmp_path / "nodes.geojson"
        _write(f, _snapshot([
            "string-not-dict",
            {"geometry": {"coordinates": [1.0]}, "properties": {"node_id": "!a1"}},  # short coords
            {"geometry": {"coordinates": [-122.0, 37.0]}, "properties": "not-dict"},
            _writer_feature(node_id="!a1b2c3d4"),  # the one good one
        ]))
        fc = MeshClientCollector(path=str(f))._fetch()
        assert [x["properties"]["id"] for x in fc["features"]] == ["!a1b2c3d4"]


class TestMeshClientCollectorOnlineThreshold:
    def _is_online(self, tmp_path, last_heard):
        f = tmp_path / "nodes.geojson"
        _write(f, _snapshot([_writer_feature(last_heard=last_heard)]))
        props = MeshClientCollector(path=str(f))._fetch()["features"][0]["properties"]
        return props.get("is_online")

    # Real writer format: ISO-8601 strings. Regression for the live-caught bug
    # where is_node_online(float(iso)) raised -> is_online was always None.
    def test_iso_recent_online(self, tmp_path):
        assert self._is_online(tmp_path, datetime.now(timezone.utc).isoformat()) is True

    def test_iso_old_offline(self, tmp_path):
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        assert self._is_online(tmp_path, old) is False

    def test_iso_z_suffix_parsed(self, tmp_path):
        iso_z = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        assert self._is_online(tmp_path, iso_z) is True

    # Numeric epoch (and numeric string) must keep working.
    def test_epoch_recent_online(self, tmp_path):
        assert self._is_online(tmp_path, time.time()) is True

    def test_epoch_old_offline(self, tmp_path):
        assert self._is_online(tmp_path, time.time() - 100000) is False

    def test_missing_last_heard_unknown(self, tmp_path):
        # No last_heard -> is_online unknown (None), never falsely online.
        # make_feature strips None, so the property is simply absent.
        f = tmp_path / "nodes.geojson"
        feat = _writer_feature()
        feat["properties"]["last_heard"] = None
        _write(f, _snapshot([feat]))
        props = MeshClientCollector(path=str(f))._fetch()["features"][0]["properties"]
        assert "is_online" not in props

    def test_threshold_registered(self):
        assert ONLINE_THRESHOLDS["mesh_client"] == 900


class TestToEpoch:
    def test_iso_with_offset(self):
        ts = MeshClientCollector._to_epoch("2026-06-21T04:10:19.295239+00:00")
        assert isinstance(ts, float) and ts > 0

    def test_iso_with_z(self):
        ts = MeshClientCollector._to_epoch("2026-06-21T04:10:19Z")
        assert isinstance(ts, float) and ts > 0

    def test_numeric_passthrough(self):
        assert MeshClientCollector._to_epoch(1750000000) == 1750000000.0
        assert MeshClientCollector._to_epoch("1750000000") == 1750000000.0

    def test_none_and_junk_return_none(self):
        assert MeshClientCollector._to_epoch(None) is None
        assert MeshClientCollector._to_epoch("") is None
        assert MeshClientCollector._to_epoch("not-a-date") is None
        assert MeshClientCollector._to_epoch(True) is None


class TestMeshClientStatusWiring:
    """mesh_client must appear consistently in the source enumerations, not
    just in the aggregator (the 'two consumers, one updated' gap)."""

    def test_get_enabled_sources_includes_mesh_client_when_enabled(self, tmp_path):
        from src.utils.config import MapsConfig
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"enable_mesh_client": True}))
        assert "mesh_client" in MapsConfig(config_path=p).get_enabled_sources()

    def test_get_enabled_sources_omits_mesh_client_by_default(self, tmp_path):
        from src.utils.config import MapsConfig
        assert "mesh_client" not in MapsConfig(config_path=tmp_path / "absent.json").get_enabled_sources()

    def test_mesh_client_is_a_valid_source_filter(self):
        from src.map_server import MapRequestHandler
        # /api/nodes/mesh_client must not 404 as an "Unknown source".
        assert "mesh_client" in MapRequestHandler._VALID_SOURCES


class TestMeshClientCollectorStaleSkip:
    def test_unchanged_updated_reuses_parse(self, tmp_path):
        f = tmp_path / "nodes.geojson"
        _write(f, _snapshot([_writer_feature()], updated="2026-06-20T01:00:00Z"))
        c = MeshClientCollector(path=str(f))
        fc1 = c._fetch()
        fc2 = c._fetch()  # file unchanged → same object reused
        assert fc1 is fc2

    def test_changed_updated_reparses(self, tmp_path):
        f = tmp_path / "nodes.geojson"
        _write(f, _snapshot([_writer_feature()], updated="2026-06-20T01:00:00Z"))
        c = MeshClientCollector(path=str(f))
        fc1 = c._fetch()
        _write(f, _snapshot([_writer_feature(), _writer_feature(node_id="!b2c3d4e5")],
                            updated="2026-06-20T02:00:00Z"))
        fc2 = c._fetch()
        assert fc1 is not fc2
        assert len(fc2["features"]) == 2


class TestMeshClientCollectorConfig:
    def test_config_defaults(self):
        assert DEFAULT_CONFIG["enable_mesh_client"] is False
        assert DEFAULT_CONFIG["mesh_client_path"] == "/var/lib/meshforge/nodes.geojson"

    def test_aggregator_wires_collector_when_enabled(self, tmp_path):
        from src.collectors.aggregator import DataAggregator
        cfg = {
            "enable_meshtastic": False, "enable_reticulum": False,
            "enable_hamclock": False, "enable_aredn": False,
            "enable_meshcore": False, "enable_noaa_alerts": False,
            "enable_mesh_client": True,
            "mesh_client_path": str(tmp_path / "nodes.geojson"),
        }
        agg = DataAggregator(cfg)
        assert "mesh_client" in agg._collectors

    def test_aggregator_omits_collector_when_disabled(self, tmp_path):
        from src.collectors.aggregator import DataAggregator
        cfg = {
            "enable_meshtastic": False, "enable_reticulum": False,
            "enable_hamclock": False, "enable_aredn": False,
            "enable_meshcore": False, "enable_noaa_alerts": False,
            "enable_mesh_client": False,
        }
        agg = DataAggregator(cfg)
        assert "mesh_client" not in agg._collectors
