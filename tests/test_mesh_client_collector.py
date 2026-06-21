"""Tests for MeshClientCollector — file-based GeoJSON ingest (issue #78)."""

import json
import time

from src.collectors.base import ONLINE_THRESHOLDS
from src.collectors.mesh_client_collector import MeshClientCollector
from src.utils.config import DEFAULT_CONFIG


def _writer_feature(node_id="!a1b2c3d4", lon=-122.4194, lat=37.7749,
                    last_heard=None, **props):
    """Build one feature in the writer's (meshing_around) shape."""
    if last_heard is None:
        last_heard = time.time()
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
    def test_recent_last_heard_online(self, tmp_path):
        f = tmp_path / "nodes.geojson"
        _write(f, _snapshot([_writer_feature(last_heard=time.time())]))
        fc = MeshClientCollector(path=str(f))._fetch()
        assert fc["features"][0]["properties"]["is_online"] is True

    def test_old_last_heard_offline(self, tmp_path):
        f = tmp_path / "nodes.geojson"
        _write(f, _snapshot([_writer_feature(last_heard=time.time() - 100000)]))
        fc = MeshClientCollector(path=str(f))._fetch()
        assert fc["features"][0]["properties"]["is_online"] is False

    def test_threshold_registered(self):
        assert ONLINE_THRESHOLDS["mesh_client"] == 900


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
