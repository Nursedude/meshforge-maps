"""Tests for NodeHistoryDB - SQLite node position history tracking."""

import time
import pytest

from src.utils.node_history import NodeHistoryDB


class TestNodeHistoryDB:
    """Core NodeHistoryDB functionality."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.db = NodeHistoryDB(
            db_path=tmp_path / "test_history.db",
            throttle_seconds=0,  # No throttle for tests
        )
        yield
        self.db.close()

    def test_record_and_count(self):
        assert self.db.observation_count == 0
        assert self.db.record_observation("!abc123", 35.0, 139.0)
        assert self.db.observation_count == 1

    def test_record_multiple_nodes(self):
        self.db.record_observation("!node1", 35.0, 139.0, network="meshtastic")
        self.db.record_observation("!node2", 40.0, -74.0, network="meshtastic")
        self.db.record_observation("!node3", 51.0, -0.1, network="reticulum")
        assert self.db.observation_count == 3
        assert self.db.node_count == 3

    def test_record_with_all_fields(self):
        result = self.db.record_observation(
            node_id="!full",
            lat=35.6895,
            lon=139.6917,
            altitude=40.0,
            network="meshtastic",
            snr=9.5,
            battery=87,
            name="TestNode-Alpha",
        )
        assert result is True
        history = self.db.get_node_history("!full")
        assert len(history) == 1
        obs = history[0]
        assert obs["latitude"] == 35.6895
        assert obs["longitude"] == 139.6917
        assert obs["altitude"] == 40.0
        assert obs["network"] == "meshtastic"
        assert obs["snr"] == 9.5
        assert obs["battery"] == 87
        assert obs["name"] == "TestNode-Alpha"

    def test_throttle_prevents_rapid_recording(self, tmp_path):
        db = NodeHistoryDB(
            db_path=tmp_path / "throttle.db",
            throttle_seconds=60,
        )
        try:
            assert db.record_observation("!node", 35.0, 139.0)
            # Second immediate record should be throttled
            assert db.record_observation("!node", 35.0, 139.0) is False
            assert db.observation_count == 1
        finally:
            db.close()

    def test_throttle_allows_different_nodes(self, tmp_path):
        db = NodeHistoryDB(
            db_path=tmp_path / "throttle2.db",
            throttle_seconds=60,
        )
        try:
            assert db.record_observation("!node1", 35.0, 139.0)
            assert db.record_observation("!node2", 40.0, -74.0)
            assert db.observation_count == 2
        finally:
            db.close()


class TestTrajectoryGeoJSON:
    """Tests for get_trajectory_geojson()."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.db = NodeHistoryDB(
            db_path=tmp_path / "trajectory.db",
            throttle_seconds=0,
        )
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
        assert len(result["features"]) == 1
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
    """Tests for get_snapshot() point-in-time queries."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.db = NodeHistoryDB(
            db_path=tmp_path / "snapshot.db",
            throttle_seconds=0,
        )
        yield
        self.db.close()

    def test_empty_snapshot(self):
        result = self.db.get_snapshot(int(time.time()))
        assert result["type"] == "FeatureCollection"
        assert result["features"] == []

    def test_snapshot_returns_most_recent(self):
        self.db.record_observation("!node", 35.0, 139.0, network="meshtastic", timestamp=1000)
        self.db.record_observation("!node", 35.5, 139.5, network="meshtastic", timestamp=2000)
        result = self.db.get_snapshot(2000)
        assert len(result["features"]) == 1
        feature = result["features"][0]
        coords = feature["geometry"]["coordinates"]
        # Should return the position at timestamp 2000
        assert coords[0] == 139.5
        assert coords[1] == 35.5

    def test_snapshot_excludes_future_data(self):
        self.db.record_observation("!node", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!node", 35.5, 139.5, timestamp=3000)
        # Snapshot at timestamp 2000 should only see the first observation
        result = self.db.get_snapshot(2000)
        assert len(result["features"]) == 1
        feature = result["features"][0]
        coords = feature["geometry"]["coordinates"]
        assert coords[0] == 139.0

    def test_snapshot_multiple_nodes(self):
        self.db.record_observation("!a", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!b", 40.0, -74.0, timestamp=1500)
        result = self.db.get_snapshot(2000)
        assert len(result["features"]) == 2
        assert result["properties"]["node_count"] == 2


class TestTrackedNodes:
    """Tests for get_tracked_nodes()."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.db = NodeHistoryDB(
            db_path=tmp_path / "tracked.db",
            throttle_seconds=0,
        )
        yield
        self.db.close()

    def test_empty_tracked(self):
        assert self.db.get_tracked_nodes() == []

    def test_tracked_nodes_with_counts(self):
        self.db.record_observation("!a", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!a", 35.1, 139.1, timestamp=2000)
        self.db.record_observation("!b", 40.0, -74.0, timestamp=1500)
        nodes = self.db.get_tracked_nodes()
        assert len(nodes) == 2
        # Find node !a
        node_a = next(n for n in nodes if n["node_id"] == "!a")
        assert node_a["observation_count"] == 2
        assert node_a["first_seen"] == 1000
        assert node_a["last_seen"] == 2000


class TestPruning:
    """Tests for prune_old_data()."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.db = NodeHistoryDB(
            db_path=tmp_path / "prune.db",
            throttle_seconds=0,
            retention_seconds=3600,
        )
        yield
        self.db.close()

    def test_prune_removes_old_data(self):
        self.db.record_observation("!old", 35.0, 139.0, timestamp=100)
        self.db.record_observation("!new", 40.0, -74.0, timestamp=int(time.time()))
        deleted = self.db.prune_old_data()
        assert deleted == 1
        assert self.db.observation_count == 1

    def test_prune_with_custom_timestamp(self):
        self.db.record_observation("!a", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!b", 40.0, -74.0, timestamp=2000)
        self.db.record_observation("!c", 51.0, -0.1, timestamp=3000)
        deleted = self.db.prune_old_data(before_timestamp=2500)
        assert deleted == 2
        assert self.db.observation_count == 1


class TestDensityPoints:
    """Tests for get_density_points() coverage heatmap data."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.db = NodeHistoryDB(
            db_path=tmp_path / "density.db",
            throttle_seconds=0,
        )
        yield
        self.db.close()

    def test_empty_returns_empty(self):
        assert self.db.get_density_points() == []

    def test_single_observation(self):
        self.db.record_observation("!a", 35.0, 139.0, timestamp=1000)
        points = self.db.get_density_points()
        assert len(points) == 1
        lat, lon, count = points[0]
        assert lat == 35.0
        assert lon == 139.0
        assert count == 1

    def test_multiple_observations_same_cell(self):
        self.db.record_observation("!a", 35.00001, 139.00001, timestamp=1000)
        self.db.record_observation("!b", 35.00002, 139.00002, timestamp=2000)
        self.db.record_observation("!c", 35.00003, 139.00003, timestamp=3000)
        # With precision=4, these round to the same cell (35.0, 139.0)
        points = self.db.get_density_points(precision=4)
        assert len(points) == 1
        assert points[0][2] == 3

    def test_different_cells(self):
        self.db.record_observation("!a", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!b", 40.0, -74.0, timestamp=2000)
        points = self.db.get_density_points()
        assert len(points) == 2

    def test_sorted_descending_by_count(self):
        self.db.record_observation("!a", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!b", 35.0, 139.0, timestamp=2000)
        self.db.record_observation("!c", 40.0, -74.0, timestamp=3000)
        points = self.db.get_density_points()
        assert points[0][2] >= points[1][2]

    def test_since_filter(self):
        self.db.record_observation("!a", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!b", 35.0, 139.0, timestamp=2000)
        self.db.record_observation("!c", 35.0, 139.0, timestamp=3000)
        points = self.db.get_density_points(since=2000)
        assert len(points) == 1
        assert points[0][2] == 2  # Only observations at 2000 and 3000

    def test_until_filter(self):
        self.db.record_observation("!a", 35.0, 139.0, timestamp=1000)
        self.db.record_observation("!b", 35.0, 139.0, timestamp=2000)
        self.db.record_observation("!c", 35.0, 139.0, timestamp=3000)
        points = self.db.get_density_points(until=2000)
        assert len(points) == 1
        assert points[0][2] == 2  # Only observations at 1000 and 2000

    def test_network_filter(self):
        self.db.record_observation("!a", 35.0, 139.0, network="meshtastic", timestamp=1000)
        self.db.record_observation("!b", 35.0, 139.0, network="reticulum", timestamp=2000)
        self.db.record_observation("!c", 40.0, -74.0, network="meshtastic", timestamp=3000)
        points = self.db.get_density_points(network="meshtastic")
        assert len(points) == 2
        total = sum(p[2] for p in points)
        assert total == 2  # Only meshtastic observations

    def test_precision_coarser(self):
        # With precision=2, 35.001 and 35.004 both round to 35.0
        self.db.record_observation("!a", 35.001, 139.001, timestamp=1000)
        self.db.record_observation("!b", 35.004, 139.004, timestamp=2000)
        points = self.db.get_density_points(precision=2)
        assert len(points) == 1
        assert points[0][2] == 2

    def test_returns_empty_when_closed(self, tmp_path):
        db = NodeHistoryDB(db_path=tmp_path / "closed_density.db", throttle_seconds=0)
        db.close()
        assert db.get_density_points() == []


class TestDBClosed:
    """Tests that operations return safely when DB is closed or unavailable."""

    def test_record_returns_false_when_closed(self, tmp_path):
        db = NodeHistoryDB(db_path=tmp_path / "closed.db", throttle_seconds=0)
        db.close()
        assert db.record_observation("!node", 35.0, 139.0) is False

    def test_trajectory_returns_empty_when_closed(self, tmp_path):
        db = NodeHistoryDB(db_path=tmp_path / "closed.db", throttle_seconds=0)
        db.close()
        result = db.get_trajectory_geojson("!node")
        assert result["type"] == "FeatureCollection"
        assert result["features"] == []

    def test_snapshot_returns_empty_when_closed(self, tmp_path):
        db = NodeHistoryDB(db_path=tmp_path / "closed.db", throttle_seconds=0)
        db.close()
        result = db.get_snapshot(int(time.time()))
        assert result["features"] == []

    def test_counts_zero_when_closed(self, tmp_path):
        db = NodeHistoryDB(db_path=tmp_path / "closed.db", throttle_seconds=0)
        db.close()
        assert db.observation_count == 0
        assert db.node_count == 0
