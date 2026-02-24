"""Tests for SharedHealthStateReader - cross-process health DB access."""

import sqlite3
import threading
import pytest

from src.utils.shared_health_state import SharedHealthStateReader


class TestSharedHealthStateReader:
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

    def test_get_service_states_empty(self, tmp_path):
        db_path = tmp_path / "health.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()
        reader = SharedHealthStateReader(db_path=db_path)
        # Table doesn't exist yet -- should return empty list
        assert reader.get_service_states() == []
        reader.close()

    def test_get_service_states_with_data(self, tmp_path):
        db_path = tmp_path / "health.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE service_health (
                service_name TEXT, status TEXT, last_updated INTEGER,
                error_count INTEGER, success_count INTEGER, metadata TEXT
            )
        """)
        conn.execute(
            "INSERT INTO service_health VALUES (?, ?, ?, ?, ?, ?)",
            ("mqtt", "healthy", 1700000000, 0, 100, "{}"),
        )
        conn.execute(
            "INSERT INTO service_health VALUES (?, ?, ?, ?, ?, ?)",
            ("gateway", "degraded", 1700000001, 5, 95, "{}"),
        )
        conn.commit()
        conn.close()

        reader = SharedHealthStateReader(db_path=db_path)
        states = reader.get_service_states()
        assert len(states) == 2
        assert states[0]["service_name"] == "gateway"  # ORDER BY name
        assert states[1]["service_name"] == "mqtt"
        assert states[1]["status"] == "healthy"
        assert states[1]["success_count"] == 100
        reader.close()

    def test_get_node_health_empty(self, tmp_path):
        db_path = tmp_path / "health.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()
        reader = SharedHealthStateReader(db_path=db_path)
        assert reader.get_node_health() == []
        reader.close()

    def test_get_node_health_with_data(self, tmp_path):
        db_path = tmp_path / "health.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE node_health (
                node_id TEXT, health_score INTEGER, status TEXT,
                last_seen INTEGER, network TEXT, metadata TEXT
            )
        """)
        conn.execute(
            "INSERT INTO node_health VALUES (?, ?, ?, ?, ?, ?)",
            ("!abc123", 95, "healthy", 1700000000, "meshtastic", "{}"),
        )
        conn.commit()
        conn.close()

        reader = SharedHealthStateReader(db_path=db_path)
        nodes = reader.get_node_health()
        assert len(nodes) == 1
        assert nodes[0]["node_id"] == "!abc123"
        assert nodes[0]["health_score"] == 95
        reader.close()

    def test_get_node_health_filtered(self, tmp_path):
        db_path = tmp_path / "health.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE node_health (
                node_id TEXT, health_score INTEGER, status TEXT,
                last_seen INTEGER, network TEXT, metadata TEXT
            )
        """)
        conn.execute(
            "INSERT INTO node_health VALUES (?, ?, ?, ?, ?, ?)",
            ("!abc", 95, "healthy", 1700000000, "meshtastic", "{}"),
        )
        conn.execute(
            "INSERT INTO node_health VALUES (?, ?, ?, ?, ?, ?)",
            ("!def", 50, "degraded", 1700000001, "reticulum", "{}"),
        )
        conn.commit()
        conn.close()

        reader = SharedHealthStateReader(db_path=db_path)
        nodes = reader.get_node_health("!abc")
        assert len(nodes) == 1
        assert nodes[0]["node_id"] == "!abc"
        reader.close()

    def test_get_latency_percentiles_empty(self, tmp_path):
        db_path = tmp_path / "health.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()
        reader = SharedHealthStateReader(db_path=db_path)
        assert reader.get_latency_percentiles() == {}
        reader.close()

    def test_get_latency_percentiles_with_data(self, tmp_path):
        db_path = tmp_path / "health.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE latency_stats (
                p50_ms REAL, p90_ms REAL, p99_ms REAL,
                sample_count INTEGER, last_updated INTEGER
            )
        """)
        conn.execute(
            "INSERT INTO latency_stats VALUES (?, ?, ?, ?, ?)",
            (50.0, 150.0, 500.0, 1000, 1700000000),
        )
        conn.commit()
        conn.close()

        reader = SharedHealthStateReader(db_path=db_path)
        latency = reader.get_latency_percentiles()
        assert latency["p50_ms"] == 50.0
        assert latency["p90_ms"] == 150.0
        assert latency["p99_ms"] == 500.0
        assert latency["sample_count"] == 1000
        reader.close()

    def test_get_summary(self, tmp_path):
        db_path = tmp_path / "health.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()
        reader = SharedHealthStateReader(db_path=db_path)
        summary = reader.get_summary()
        assert summary["available"] is True
        assert isinstance(summary["services"], list)
        assert isinstance(summary["latency"], dict)
        assert "checked_at" in summary
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
        assert reader.get_service_states() == []

    def test_concurrent_refresh_is_thread_safe(self, tmp_path):
        """Multiple threads calling refresh() should not corrupt state."""
        db_path = tmp_path / "health.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()

        reader = SharedHealthStateReader(db_path=db_path)
        reader.close()  # Reset to unavailable

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
