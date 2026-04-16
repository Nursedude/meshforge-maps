"""Tests for BaseCollector persistent cache (Step 14)."""

import json
import time
from typing import Any, Dict


from src.collectors.base import BaseCollector, make_feature_collection


class _TestCollector(BaseCollector):
    """Concrete test collector for testing BaseCollector."""

    source_name = "test_source"

    def __init__(self, data=None, **kwargs):
        self._test_data = data or make_feature_collection([], "test_source")
        super().__init__(**kwargs)

    def _fetch(self) -> Dict[str, Any]:
        return self._test_data


class TestPersistentCacheLoad:
    """Tests for loading persistent cache on startup."""

    def test_no_cache_file_starts_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.collectors.base.get_data_dir", lambda: tmp_path,
        )
        collector = _TestCollector()
        assert collector._cache is None

    def test_valid_cache_loaded_on_startup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.collectors.base.get_data_dir", lambda: tmp_path,
        )
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "test_source.json"
        data = make_feature_collection(
            [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]},
              "properties": {"id": "!a"}}],
            "test_source",
        )
        cache_file.write_text(json.dumps(data))

        collector = _TestCollector(persistent_cache=True)
        assert collector._cache is not None
        assert collector._cache["type"] == "FeatureCollection"
        assert len(collector._cache["features"]) == 1

    def test_stale_cache_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.collectors.base.get_data_dir", lambda: tmp_path,
        )
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "test_source.json"
        data = make_feature_collection([], "test_source")
        cache_file.write_text(json.dumps(data))

        # Make the file look old
        import os
        old_time = time.time() - 20000  # ~5.5 hours old
        os.utime(cache_file, (old_time, old_time))

        collector = _TestCollector(persistent_cache=True)
        assert collector._cache is None

    def test_invalid_json_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.collectors.base.get_data_dir", lambda: tmp_path,
        )
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "test_source.json"
        cache_file.write_text("NOT VALID JSON")

        collector = _TestCollector(persistent_cache=True)
        assert collector._cache is None

    def test_non_feature_collection_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.collectors.base.get_data_dir", lambda: tmp_path,
        )
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "test_source.json"
        cache_file.write_text(json.dumps({"type": "SomethingElse"}))

        collector = _TestCollector(persistent_cache=True)
        assert collector._cache is None


class TestPersistentCacheSave:
    """Tests for saving cache to disk after successful fetch."""

    def test_cache_saved_on_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.collectors.base.get_data_dir", lambda: tmp_path,
        )
        data = make_feature_collection(
            [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]},
              "properties": {"id": "!a"}}],
            "test_source",
        )
        collector = _TestCollector(data=data, cache_ttl_seconds=0, persistent_cache=True)
        collector.collect()

        cache_file = tmp_path / "cache" / "test_source.json"
        assert cache_file.exists()

        saved = json.loads(cache_file.read_text())
        assert saved["type"] == "FeatureCollection"
        assert len(saved["features"]) == 1

    def test_persistent_cache_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.collectors.base.get_data_dir", lambda: tmp_path,
        )
        data = make_feature_collection([], "test_source")
        collector = _TestCollector(
            data=data, cache_ttl_seconds=0, persistent_cache=False,
        )
        collector.collect()

        cache_file = tmp_path / "cache" / "test_source.json"
        assert not cache_file.exists()
