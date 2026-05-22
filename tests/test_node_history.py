"""Tests for NodeHistoryDB v2 — movement-triggered trajectory + per-node cap."""

import sqlite3
import time

import pytest

from src.utils.node_history import (
    NodeHistoryDB,
    _haversine_meters,
    _USER_VERSION,
)


class TestHaversineHelper:
    def test_zero_distance(self):
        assert _haversine_meters(35.0, 139.0, 35.0, 139.0) == 0.0

    def test_known_short_distance(self):
        # ~111 meters at the equator per 0.001° of latitude
        d = _haversine_meters(0.0, 0.0, 0.001, 0.0)
        assert 110 < d < 112

    def test_symmetric(self):
        d1 = _haversine_meters(35.0, 139.0, 36.0, 140.0)
        d2 = _haversine_meters(36.0, 140.0, 35.0, 139.0)
        assert d1 == pytest.approx(d2)

    def test_long_distance_within_one_percent(self):
        # San Francisco (37.7749, -122.4194) to New York (40.7128, -74.0060)
        # Reference: ~4,129 km
        d = _haversine_meters(37.7749, -122.4194, 40.7128, -74.0060)
        assert 4_080_000 < d < 4_180_000


class TestSchemaAndMigration:
    def test_fresh_db_creates_v2_schema(self, tmp_path):
        db_path = tmp_path / "fresh.db"
        db = NodeHistoryDB(db_path=db_path)
        try:
            tables = {row[0] for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            assert "nodes_current" in tables
            assert "trajectory" in tables
            assert "observations" not in tables
            version = db._conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == _USER_VERSION
        finally:
            db.close()

    def test_migration_from_v1_seeds_nodes_current(self, tmp_path):
        # Build a v1-style DB by hand.
        db_path = tmp_path / "v1.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                altitude REAL,
                network TEXT,
                snr REAL,
                battery INTEGER,
                name TEXT
            )
        """)
        # Two nodes, multiple rows each.
        conn.executemany(
            "INSERT INTO observations (node_id, timestamp, latitude, longitude, network) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("!a", 1000, 35.0, 139.0, "meshtastic"),
                ("!a", 2000, 35.1, 139.1, "meshtastic"),
                ("!a", 3000, 35.2, 139.2, "meshtastic"),
                ("!b", 1500, 40.0, -74.0, "reticulum"),
            ],
        )
        conn.commit()
        conn.close()

        db = NodeHistoryDB(db_path=db_path)
        try:
            tables = {row[0] for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            assert "observations" not in tables, "v1 table should be dropped"
            assert "nodes_current" in tables
            assert "trajectory" in tables
            rows = db._conn.execute(
                "SELECT node_id, timestamp, latitude, longitude, network "
                "FROM nodes_current ORDER BY node_id"
            ).fetchall()
            assert rows == [
                ("!a", 3000, 35.2, 139.2, "meshtastic"),
                ("!b", 1500, 40.0, -74.0, "reticulum"),
            ]
            # trajectory starts empty in the fresh-start migration
            traj_count = db._conn.execute(
                "SELECT COUNT(*) FROM trajectory"
            ).fetchone()[0]
            assert traj_count == 0
            version = db._conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == _USER_VERSION
        finally:
            db.close()

    def test_migration_idempotent(self, tmp_path):
        db_path = tmp_path / "idem.db"
        db1 = NodeHistoryDB(db_path=db_path)
        db1.record_observation("!a", 35.0, 139.0)
        db1.close()
        # Second open should not re-run migration or wipe state
        db2 = NodeHistoryDB(db_path=db_path)
        try:
            rows = db2._conn.execute(
                "SELECT node_id FROM nodes_current"
            ).fetchall()
            assert rows == [("!a",)]
        finally:
            db2.close()


class TestUpsertCurrent:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.db = NodeHistoryDB(db_path=tmp_path / "upsert.db")
        yield
        self.db.close()

    def test_first_observation_creates_nodes_current_row(self):
        assert self.db.record_observation(
            "!a", 35.0, 139.0, network="meshtastic", timestamp=1000,
        )
        row = self.db._conn.execute(
            "SELECT node_id, timestamp, latitude, longitude, network FROM nodes_current"
        ).fetchone()
        assert row == ("!a", 1000, 35.0, 139.0, "meshtastic")

    def test_second_observation_overwrites_previous(self):
        self.db.record_observation("!a", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!a", 35.0001, 139.0001, timestamp=2000)
        rows = self.db._conn.execute(
            "SELECT node_id, timestamp, latitude, longitude FROM nodes_current"
        ).fetchall()
        # One row only; the new timestamp wins
        assert len(rows) == 1
        assert rows[0] == ("!a", 2000, 35.0001, 139.0001)

    def test_multiple_nodes_each_get_one_row(self):
        self.db.record_observation("!a", 35.0, 139.0)
        self.db.record_observation("!b", 40.0, -74.0)
        self.db.record_observation("!a", 35.001, 139.001)
        count = self.db._conn.execute(
            "SELECT COUNT(*) FROM nodes_current"
        ).fetchone()[0]
        assert count == 2

    def test_node_count_matches_nodes_current(self):
        for i in range(5):
            self.db.record_observation(f"!n{i}", 35.0 + i, 139.0 + i)
        assert self.db.node_count == 5


class TestMovementThreshold:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        # 50 m default threshold
        self.db = NodeHistoryDB(db_path=tmp_path / "movement.db")
        yield
        self.db.close()

    def test_first_observation_always_appends_trajectory(self):
        self.db.record_observation("!a", 35.0, 139.0)
        count = self.db._conn.execute(
            "SELECT COUNT(*) FROM trajectory WHERE node_id = '!a'"
        ).fetchone()[0]
        assert count == 1

    def test_jitter_below_threshold_is_skipped(self):
        # ~1 m apart (under 50 m threshold)
        self.db.record_observation("!a", 35.0, 139.0)
        self.db.record_observation("!a", 35.00001, 139.00001)
        self.db.record_observation("!a", 35.00002, 139.00002)
        count = self.db._conn.execute(
            "SELECT COUNT(*) FROM trajectory WHERE node_id = '!a'"
        ).fetchone()[0]
        assert count == 1, "Sub-threshold jitter must not write trajectory rows"

    def test_movement_above_threshold_appends(self):
        # ~1110 m apart (well past 50 m)
        self.db.record_observation("!a", 35.0, 139.0)
        self.db.record_observation("!a", 35.01, 139.0)
        count = self.db._conn.execute(
            "SELECT COUNT(*) FROM trajectory WHERE node_id = '!a'"
        ).fetchone()[0]
        assert count == 2

    def test_configurable_threshold(self, tmp_path):
        # Tight 5 m threshold catches the jitter that the default skips
        db = NodeHistoryDB(
            db_path=tmp_path / "tight.db",
            move_threshold_meters=5.0,
        )
        try:
            db.record_observation("!a", 35.0, 139.0)
            # ~11 m at 0.0001°
            db.record_observation("!a", 35.0001, 139.0001)
            count = db._conn.execute(
                "SELECT COUNT(*) FROM trajectory WHERE node_id = '!a'"
            ).fetchone()[0]
            assert count == 2
        finally:
            db.close()

    def test_upsert_still_runs_even_when_trajectory_skipped(self):
        self.db.record_observation("!a", 35.0, 139.0, timestamp=1000)
        # Below threshold — trajectory skipped, but nodes_current must still
        # advance the timestamp so the snapshot stays fresh.
        self.db.record_observation("!a", 35.00001, 139.00001, timestamp=2000)
        ts = self.db._conn.execute(
            "SELECT timestamp FROM nodes_current WHERE node_id = '!a'"
        ).fetchone()[0]
        assert ts == 2000
        traj_count = self.db._conn.execute(
            "SELECT COUNT(*) FROM trajectory WHERE node_id = '!a'"
        ).fetchone()[0]
        assert traj_count == 1  # only the first one


class TestPerNodeCap:
    def test_cap_evicts_oldest_row_for_that_node(self, tmp_path):
        db = NodeHistoryDB(
            db_path=tmp_path / "cap.db",
            trajectory_rows_per_node=3,
        )
        try:
            # 5 distinct positions, all > 50 m apart
            for i in range(5):
                db.record_observation("!a", 35.0 + 0.01 * i, 139.0)
            count = db._conn.execute(
                "SELECT COUNT(*) FROM trajectory WHERE node_id = '!a'"
            ).fetchone()[0]
            assert count == 3, "Per-node cap must hold"
            # Oldest two (35.0, 35.01) evicted; newest three (35.02, 35.03, 35.04) kept
            lats = [r[0] for r in db._conn.execute(
                "SELECT latitude FROM trajectory WHERE node_id = '!a' ORDER BY id ASC"
            ).fetchall()]
            assert lats == [pytest.approx(35.02), pytest.approx(35.03), pytest.approx(35.04)]
        finally:
            db.close()

    def test_cap_is_per_node(self, tmp_path):
        db = NodeHistoryDB(
            db_path=tmp_path / "cap_per_node.db",
            trajectory_rows_per_node=2,
        )
        try:
            for i in range(3):
                db.record_observation("!a", 35.0 + 0.01 * i, 139.0)
            for i in range(3):
                db.record_observation("!b", 40.0 + 0.01 * i, -74.0)
            count_a = db._conn.execute(
                "SELECT COUNT(*) FROM trajectory WHERE node_id = '!a'"
            ).fetchone()[0]
            count_b = db._conn.execute(
                "SELECT COUNT(*) FROM trajectory WHERE node_id = '!b'"
            ).fetchone()[0]
            assert count_a == 2
            assert count_b == 2
        finally:
            db.close()


class TestBatchRecording:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.db = NodeHistoryDB(db_path=tmp_path / "batch.db")
        yield
        self.db.close()

    def test_batch_upserts_all_nodes_current(self):
        self.db.record_observations_batch([
            {"node_id": "!a", "lat": 35.0, "lon": 139.0, "network": "m"},
            {"node_id": "!b", "lat": 40.0, "lon": -74.0, "network": "m"},
            {"node_id": "!c", "lat": 51.0, "lon": -0.1, "network": "r"},
        ])
        count = self.db._conn.execute(
            "SELECT COUNT(*) FROM nodes_current"
        ).fetchone()[0]
        assert count == 3

    def test_batch_empty_list_returns_zero(self):
        assert self.db.record_observations_batch([]) == 0

    def test_batch_skips_missing_node_id(self):
        appended = self.db.record_observations_batch([
            {"node_id": "!a", "lat": 35.0, "lon": 139.0},
            {"lat": 40.0, "lon": -74.0},  # missing node_id
            {"node_id": "!c", "lat": 51.0, "lon": -0.1},
        ])
        assert appended == 2

    def test_batch_returns_trajectory_append_count(self):
        # All three are first-sightings, all append trajectory
        appended = self.db.record_observations_batch([
            {"node_id": f"!n{i}", "lat": 35.0 + i, "lon": 139.0 + i}
            for i in range(3)
        ])
        assert appended == 3

    def test_batch_applies_move_threshold(self):
        # First batch: all first-sightings → all 3 trajectory rows
        self.db.record_observations_batch([
            {"node_id": "!a", "lat": 35.0, "lon": 139.0},
            {"node_id": "!b", "lat": 40.0, "lon": -74.0},
            {"node_id": "!c", "lat": 51.0, "lon": -0.1},
        ])
        # Second batch: same positions ± jitter → all under threshold, zero appends
        appended = self.db.record_observations_batch([
            {"node_id": "!a", "lat": 35.00001, "lon": 139.00001},
            {"node_id": "!b", "lat": 40.00001, "lon": -74.00001},
            {"node_id": "!c", "lat": 51.00001, "lon": -0.10001},
        ])
        assert appended == 0


class TestTrajectoryGeoJSON:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.db = NodeHistoryDB(db_path=tmp_path / "trajectory.db")
        yield
        self.db.close()

    def test_empty_trajectory(self):
        result = self.db.get_trajectory_geojson("!nonexistent")
        assert result["type"] == "FeatureCollection"
        assert result["features"] == []

    def test_single_point_trajectory(self):
        self.db.record_observation("!node", 35.0, 139.0, timestamp=1000)
        result = self.db.get_trajectory_geojson("!node")
        assert result["type"] == "FeatureCollection"
        assert len(result["features"]) == 1
        feature = result["features"][0]
        assert feature["geometry"]["type"] == "Point"
        assert feature["geometry"]["coordinates"] == [139.0, 35.0]
        assert feature["properties"]["node_id"] == "!node"
        assert feature["properties"]["point_count"] == 1

    def test_multi_point_trajectory(self):
        self.db.record_observation("!moving", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!moving", 35.1, 139.1, timestamp=2000)
        self.db.record_observation("!moving", 35.2, 139.2, timestamp=3000)
        result = self.db.get_trajectory_geojson("!moving")
        feature = result["features"][0]
        assert feature["geometry"]["type"] == "LineString"
        coords = feature["geometry"]["coordinates"]
        assert len(coords) == 3
        assert coords[0] == [139.0, 35.0]
        assert coords[1] == [139.1, 35.1]
        assert coords[2] == [139.2, 35.2]
        props = feature["properties"]
        assert props["point_count"] == 3
        assert props["first_seen"] == 1000
        assert props["last_seen"] == 3000
        assert props["time_span_seconds"] == 2000

    def test_trajectory_with_altitude(self):
        self.db.record_observation("!alt", 35.0, 139.0, altitude=100.0, timestamp=1000)
        self.db.record_observation("!alt", 35.1, 139.1, altitude=200.0, timestamp=2000)
        result = self.db.get_trajectory_geojson("!alt")
        feature = result["features"][0]
        coords = feature["geometry"]["coordinates"]
        assert coords[0] == [139.0, 35.0, 100.0]
        assert coords[1] == [139.1, 35.1, 200.0]

    def test_trajectory_since_filter(self):
        self.db.record_observation("!node", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!node", 35.1, 139.1, timestamp=2000)
        self.db.record_observation("!node", 35.2, 139.2, timestamp=3000)
        result = self.db.get_trajectory_geojson("!node", since=2000)
        feature = result["features"][0]
        assert feature["properties"]["point_count"] == 2

    def test_trajectory_until_filter(self):
        self.db.record_observation("!node", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!node", 35.1, 139.1, timestamp=2000)
        self.db.record_observation("!node", 35.2, 139.2, timestamp=3000)
        result = self.db.get_trajectory_geojson("!node", until=2000)
        feature = result["features"][0]
        assert feature["properties"]["point_count"] == 2


class TestSnapshot:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.db = NodeHistoryDB(db_path=tmp_path / "snapshot.db")
        yield
        self.db.close()

    def test_empty_snapshot(self):
        result = self.db.get_snapshot(int(time.time()))
        assert result["type"] == "FeatureCollection"
        assert result["features"] == []

    def test_current_snapshot_uses_nodes_current(self):
        self.db.record_observation("!a", 35.0, 139.0, network="meshtastic", timestamp=1000)
        # Future timestamp → reads nodes_current
        result = self.db.get_snapshot(int(time.time()) + 3600)
        assert len(result["features"]) == 1
        coords = result["features"][0]["geometry"]["coordinates"]
        assert coords == [139.0, 35.0]

    def test_past_snapshot_uses_trajectory(self):
        self.db.record_observation("!node", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!node", 35.5, 139.5, timestamp=3000)
        # Snapshot at timestamp 2000 should only see the first observation
        result = self.db.get_snapshot(2000)
        assert len(result["features"]) == 1
        coords = result["features"][0]["geometry"]["coordinates"]
        assert coords[0] == 139.0
        assert coords[1] == 35.0

    def test_snapshot_multiple_nodes(self):
        # Distant positions so both trajectory writes happen
        self.db.record_observation("!a", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!b", 40.0, -74.0, timestamp=1500)
        result = self.db.get_snapshot(int(time.time()) + 3600)
        assert len(result["features"]) == 2
        assert result["properties"]["node_count"] == 2


class TestTrackedNodes:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.db = NodeHistoryDB(db_path=tmp_path / "tracked.db")
        yield
        self.db.close()

    def test_empty_tracked(self):
        assert self.db.get_tracked_nodes() == []

    def test_tracked_counts_trajectory_appends_not_messages(self):
        # 1st append (first sighting) + 1 movement above threshold = 2 trajectory rows
        self.db.record_observation("!a", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!a", 35.00001, 139.00001, timestamp=1500)  # jitter, skipped
        self.db.record_observation("!a", 35.1, 139.1, timestamp=2000)         # movement
        # Static node — only first sighting → 1 trajectory row
        self.db.record_observation("!b", 40.0, -74.0, timestamp=1500)
        nodes = self.db.get_tracked_nodes()
        assert len(nodes) == 2
        a = next(n for n in nodes if n["node_id"] == "!a")
        b = next(n for n in nodes if n["node_id"] == "!b")
        assert a["observation_count"] == 2
        assert b["observation_count"] == 1


class TestPruning:
    def test_prune_removes_old_trajectory_rows(self, tmp_path):
        db = NodeHistoryDB(
            db_path=tmp_path / "prune.db",
            retention_seconds=3600,
        )
        try:
            db.record_observation("!old", 35.0, 139.0, timestamp=100)
            db.record_observation("!new", 40.0, -74.0, timestamp=int(time.time()))
            deleted = db.prune_old_data()
            assert deleted == 1
            # nodes_current is NOT pruned by time — both rows remain
            assert db.node_count == 2
            # trajectory has only the recent row
            assert db.observation_count == 1
        finally:
            db.close()

    def test_prune_with_custom_timestamp(self, tmp_path):
        db = NodeHistoryDB(db_path=tmp_path / "prune_custom.db")
        try:
            db.record_observation("!a", 35.0, 139.0, timestamp=1000)
            db.record_observation("!b", 40.0, -74.0, timestamp=2000)
            db.record_observation("!c", 51.0, -0.1, timestamp=3000)
            deleted = db.prune_old_data(before_timestamp=2500)
            assert deleted == 2
            assert db.observation_count == 1
        finally:
            db.close()


class TestDensityPoints:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.db = NodeHistoryDB(db_path=tmp_path / "density.db")
        yield
        self.db.close()

    def test_empty_returns_empty(self):
        assert self.db.get_density_points() == []

    def test_first_observation_counts(self):
        self.db.record_observation("!a", 35.0, 139.0)
        points = self.db.get_density_points()
        assert len(points) == 1
        lat, lon, count = points[0]
        assert lat == 35.0
        assert lon == 139.0
        assert count == 1

    def test_movements_at_different_cells(self):
        self.db.record_observation("!a", 35.0, 139.0)
        self.db.record_observation("!b", 40.0, -74.0)
        points = self.db.get_density_points()
        assert len(points) == 2


class TestWriteErrorSurfacing:
    """Disk-fatal write errors are visible to /api/health."""

    def test_initial_state_clean(self, tmp_path):
        db = NodeHistoryDB(db_path=tmp_path / "clean.db")
        try:
            state = db.write_error_state()
            assert state["last_write_error_at"] is None
            assert state["last_write_error_msg"] is None
        finally:
            db.close()

    def test_successful_write_clears_error(self, tmp_path):
        db = NodeHistoryDB(db_path=tmp_path / "ok.db")
        try:
            db._last_write_error_at = 12345
            db._last_write_error_msg = "stale"
            assert db.record_observation("!ok", 35.0, 139.0) is True
            state = db.write_error_state()
            assert state["last_write_error_at"] is None
            assert state["last_write_error_msg"] is None
        finally:
            db.close()

    def test_disk_full_error_is_recorded(self, tmp_path):
        db = NodeHistoryDB(db_path=tmp_path / "full.db")
        try:
            db._record_write_error(
                sqlite3.OperationalError("database or disk is full"),
                "!stuck",
            )
            state = db.write_error_state()
            assert state["last_write_error_at"] is not None
            assert "disk is full" in (state["last_write_error_msg"] or "")
        finally:
            db.close()

    def test_non_disk_error_does_not_set_disk_state(self, tmp_path):
        db = NodeHistoryDB(db_path=tmp_path / "other.db")
        try:
            db._record_write_error(
                sqlite3.OperationalError("table observations is locked"),
                "!nope",
            )
            state = db.write_error_state()
            assert state["last_write_error_at"] is None
            assert state["last_write_error_msg"] is None
        finally:
            db.close()

    def test_disk_io_error_message_variant_is_recorded(self, tmp_path):
        db = NodeHistoryDB(db_path=tmp_path / "io.db")
        try:
            db._record_write_error(
                sqlite3.OperationalError("disk I/O error"),
                "!io",
            )
            state = db.write_error_state()
            assert state["last_write_error_at"] is not None
            assert "I/O error" in (state["last_write_error_msg"] or "")
        finally:
            db.close()


class TestDBBackup:
    def test_create_backup(self, tmp_path):
        db = NodeHistoryDB(db_path=tmp_path / "main.db")
        db.record_observation("!node1", 35.0, 139.0)
        db.record_observation("!node2", 40.0, -74.0)
        backup_path = tmp_path / "backup.db"
        assert db.create_backup(backup_path) is True
        assert backup_path.exists()
        backup_db = NodeHistoryDB(db_path=backup_path)
        try:
            assert backup_db.node_count == 2
        finally:
            backup_db.close()
            db.close()

    def test_backup_returns_false_when_closed(self, tmp_path):
        db = NodeHistoryDB(db_path=tmp_path / "closed.db")
        db.close()
        assert db.create_backup(tmp_path / "backup.db") is False


class TestDeprecatedConstructorArgs:
    """v1 throttle_seconds / heartbeat_seconds should be accepted and ignored."""

    def test_throttle_seconds_ignored(self, tmp_path):
        db = NodeHistoryDB(
            db_path=tmp_path / "compat.db",
            throttle_seconds=300,
            heartbeat_seconds=3600,
        )
        try:
            # Behavior is v2: first append always lands
            assert db.record_observation("!a", 35.0, 139.0) is True
            count = db._conn.execute(
                "SELECT COUNT(*) FROM trajectory"
            ).fetchone()[0]
            assert count == 1
        finally:
            db.close()
