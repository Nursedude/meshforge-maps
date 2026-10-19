"""Tests for SharedHealthStateReader - cross-process health DB access."""

import sqlite3
import threading

from src.utils.shared_health_state import SharedHealthStateReader


class TestSharedHealthStateReader:
    # The schema-shaped tests (service_health columns, node_health,
    # latency_stats) were DELETED 2026-10-18: they built a schema MeshForge
    # never creates and so pinned the defect. The schema contract lives in
    # tests/test_core_health_contract.py, against core's real producer.
    """Tests for reading MeshForge core's health state DB."""

    def test_unavailable_when_no_db(self, tmp_path):
        reader = SharedHealthStateReader(db_path=tmp_path / "nonexistent.db")
        assert reader.available is False
        reader.close()

    def test_available_with_empty_db(self, tmp_path):
        # Create an empty SQLite DB
        db_path = tmp_path / "health_state.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE dummy (id INTEGER)")
        conn.commit()
        conn.close()

        reader = SharedHealthStateReader(db_path=db_path)
        assert reader.available is True
        reader.close()

    def test_summary_unavailable(self, tmp_path):
        reader = SharedHealthStateReader(db_path=tmp_path / "missing.db")
        summary = reader.get_summary()
        assert summary["available"] is False
        reader.close()

    def test_refresh_after_db_created(self, tmp_path):
        db_path = tmp_path / "late.db"
        reader = SharedHealthStateReader(db_path=db_path)
        assert reader.available is False

        # Create DB after reader was initialized
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.commit()
        conn.close()

        assert reader.refresh() is True
        assert reader.available is True
        reader.close()

    def test_close_and_operations(self, tmp_path):
        db_path = tmp_path / "health.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()
        reader = SharedHealthStateReader(db_path=db_path)
        reader.close()
        assert reader.available is False
        assert reader.get_summary()["services"] == []

    def test_concurrent_refresh_is_thread_safe(self, tmp_path):
        """Multiple threads calling refresh() should not corrupt state."""
        db_path = tmp_path / "health.db"
        # Unavailable first: the DB does not exist yet. (close() no longer
        # works for this — close means close; refresh() never reopens.)
        reader = SharedHealthStateReader(db_path=db_path)
        assert reader.available is False
        conn = sqlite3.connect(str(db_path))
        conn.close()

        errors = []

        def _refresh():
            try:
                reader.refresh()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_refresh) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Concurrent refresh raised: {errors}"
        assert reader.available is True
        reader.close()

    def test_concurrent_close_is_thread_safe(self, tmp_path):
        """Multiple threads calling close() should not raise."""
        db_path = tmp_path / "health.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()

        reader = SharedHealthStateReader(db_path=db_path)
        errors = []

        def _close():
            try:
                reader.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_close) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Concurrent close raised: {errors}"
        assert reader.available is False


class TestConnectTunedIntegration:
    """Confirm the reader routes through db_helpers.connect_tuned per CLAUDE.md
    ("anything new starts with connect_tuned"), not a hand-rolled sqlite3.connect."""

    def test_uses_connect_tuned(self, tmp_path):
        from unittest.mock import patch
        db_path = tmp_path / "health.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE dummy (id INTEGER)")
        conn.commit()
        conn.close()

        with patch(
            "src.utils.shared_health_state.connect_tuned",
            wraps=__import__(
                "src.utils.db_helpers", fromlist=["connect_tuned"],
            ).connect_tuned,
        ) as spy:
            reader = SharedHealthStateReader(db_path=db_path)
            try:
                assert reader.available is True
                spy.assert_called_once()
                # read_only=True and a small busy_timeout
                _, kwargs = spy.call_args
                assert kwargs.get("read_only") is True
                assert kwargs.get("check_same_thread") is False
                assert kwargs.get("busy_timeout_seconds") == 1.0
            finally:
                reader.close()
