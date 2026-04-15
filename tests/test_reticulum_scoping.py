"""Tests for ReticulumCollector region scoping (remote sources only)."""

from unittest.mock import patch

from src.collectors.reticulum_collector import ReticulumCollector


def _feat(node_id, lat, lon, source):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"id": node_id, "network": "reticulum", "source": source},
    }


class TestReticulumRegionScoping:
    def test_scope_filters_remote_features(self):
        c = ReticulumCollector(region_bboxes=[[18.5, -161, 22.5, -154]])
        features = [
            _feat("a", 20.0, -157.0, "rmap_world"),  # in Hawaii
            _feat("b", 40.0, -100.0, "rmap_world"),  # not in Hawaii
        ]
        kept = c._scope(features)
        assert [f["properties"]["id"] for f in kept] == ["a"]

    def test_no_bboxes_is_passthrough(self):
        c = ReticulumCollector(region_bboxes=None)
        features = [_feat("a", 40.0, -100.0, "rmap_world")]
        assert c._scope(features) == features

    def test_local_sources_unaffected(self):
        # _fetch_from_rnstatus and _read_cache_file are NOT passed through _scope —
        # verify by construction: _fetch() only wraps rch + rmap with _scope.
        c = ReticulumCollector(region_bboxes=[[0, 0, 1, 1]])
        with patch.object(c, "_fetch_from_rnstatus", return_value=[_feat("local", 40, -100, "rnstatus")]), \
             patch.object(c, "_fetch_from_rch", return_value=[]), \
             patch.object(c, "_fetch_from_rmap_world", return_value=[]), \
             patch.object(c, "_fetch_from_cache", return_value=[]), \
             patch.object(c, "_fetch_from_unified_cache", return_value=[]):
            result = c._fetch()
        ids = [f["properties"]["id"] for f in result["features"]]
        assert "local" in ids  # local node kept despite being outside bbox
