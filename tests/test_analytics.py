"""Tests for historical analytics: time-series aggregation, network growth, trends."""

import time
from pathlib import Path

import pytest

from src.utils.analytics import HistoricalAnalytics
from src.utils.alert_engine import AlertEngine
from src.utils.node_history import NodeHistoryDB


@pytest.fixture
def db(tmp_path):
    """Create a temporary NodeHistoryDB with test data."""
    db_path = tmp_path / "test_history.db"
    history = NodeHistoryDB(db_path=db_path, throttle_seconds=0)
    yield history
    history.close()


@pytest.fixture
def populated_db(db):
    """NodeHistoryDB pre-populated with test observations."""
    now = int(time.time())
    # 24 hours of data, one observation per hour for 3 nodes
    for hour in range(24):
        ts = now - (23 - hour) * 3600
        db.record_observation(
            "!node_a", lat=40.0, lon=-105.0, network="meshtastic",
            timestamp=ts,
        )
        db.record_observation(
            "!node_b", lat=41.0, lon=-104.0, network="meshtastic",
            timestamp=ts,
        )
        if hour >= 12:  # Node C only appears in last 12 hours
            db.record_observation(
                "!node_c", lat=42.0, lon=-103.0, network="reticulum",
                timestamp=ts,
            )
    return db


@pytest.fixture
def alert_engine():
    """AlertEngine with some test alerts."""
    engine = AlertEngine()
    engine.clear_cooldowns()
    # Trigger some alerts
    engine.evaluate_node("!a1", {"battery": 3}, now=time.time() - 7200)
    engine.clear_cooldowns()
    engine.evaluate_node("!a2", {"battery": 15}, now=time.time() - 3600)
    engine.clear_cooldowns()
    engine.evaluate_node("!a3", {"snr": -15}, now=time.time() - 1800)
    engine.clear_cooldowns()
    engine.evaluate_node("!a4", {"battery": 1}, now=time.time())
    return engine


# ---------------------------------------------------------------------------
# Network growth
# ---------------------------------------------------------------------------


class TestNetworkGrowth:
    def test_basic_growth(self, populated_db):
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.network_growth(bucket_seconds=3600)

        assert "buckets" in result
        assert len(result["buckets"]) > 0
        assert result["bucket_seconds"] == 3600

    def test_growth_bucket_contains_unique_nodes(self, populated_db):
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.network_growth(bucket_seconds=3600)

        for bucket in result["buckets"]:
            assert "unique_nodes" in bucket
            assert "observations" in bucket
            assert "timestamp" in bucket
            assert bucket["unique_nodes"] > 0

    def test_growth_with_custom_time_range(self, populated_db):
        now = int(time.time())
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.network_growth(
            since=now - 3600,
            until=now,
            bucket_seconds=1800,
        )

        assert result["since"] == now - 3600
        assert result["until"] == now
        assert result["bucket_seconds"] == 1800

    def test_growth_empty_range(self, db):
        analytics = HistoricalAnalytics(node_history=db)
        result = analytics.network_growth(
            since=0, until=100,
        )
        assert result["buckets"] == []

    def test_growth_no_history(self):
        analytics = HistoricalAnalytics(node_history=None)
        result = analytics.network_growth()
        assert "error" in result

    def test_growth_bucket_size_clamped(self, populated_db):
        analytics = HistoricalAnalytics(node_history=populated_db)

        # Too small bucket should be clamped to 60
        result = analytics.network_growth(bucket_seconds=1)
        assert result["bucket_seconds"] == 60

        # Too large bucket should be clamped to 86400
        result = analytics.network_growth(bucket_seconds=999999)
        assert result["bucket_seconds"] == 86400

    def test_growth_shows_network_expansion(self, populated_db):
        """Later buckets should show more nodes (node_c joins at hour 12)."""
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.network_growth(bucket_seconds=3600)

        buckets = result["buckets"]
        if len(buckets) >= 2:
            # Early buckets: 2 nodes (A, B)
            # Late buckets: 3 nodes (A, B, C)
            early = buckets[0]
            late = buckets[-1]
            # Node C joins halfway through, so late buckets should have >= early
            assert late["unique_nodes"] >= early["unique_nodes"]


# ---------------------------------------------------------------------------
# Activity heatmap
# ---------------------------------------------------------------------------


class TestActivityHeatmap:
    def test_basic_heatmap(self, populated_db):
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.activity_heatmap()

        assert "hours" in result
        assert len(result["hours"]) == 24
        assert result["total_observations"] > 0

    def test_heatmap_has_peak_hour(self, populated_db):
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.activity_heatmap()

        assert result["peak_hour"] is not None
        assert 0 <= result["peak_hour"] < 24

    def test_heatmap_no_history(self):
        analytics = HistoricalAnalytics(node_history=None)
        result = analytics.activity_heatmap()
        assert "error" in result
        assert result["hours"] == [0] * 24

    def test_heatmap_empty_range(self, db):
        analytics = HistoricalAnalytics(node_history=db)
        result = analytics.activity_heatmap(since=0, until=100)
        assert result["total_observations"] == 0

    def test_heatmap_custom_time_range(self, populated_db):
        now = int(time.time())
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.activity_heatmap(
            since=now - 3600, until=now,
        )
        assert result["since"] == now - 3600


# ---------------------------------------------------------------------------
# Node activity ranking
# ---------------------------------------------------------------------------


class TestNodeActivityRanking:
    def test_basic_ranking(self, populated_db):
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.node_activity_ranking()

        assert "nodes" in result
        assert len(result["nodes"]) == 3  # A, B, C

    def test_ranking_ordered_by_count(self, populated_db):
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.node_activity_ranking()

        nodes = result["nodes"]
        for i in range(len(nodes) - 1):
            assert nodes[i]["observation_count"] >= nodes[i + 1]["observation_count"]

    def test_ranking_node_fields(self, populated_db):
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.node_activity_ranking()

        for node in result["nodes"]:
            assert "node_id" in node
            assert "observation_count" in node
            assert "first_seen" in node
            assert "last_seen" in node
            assert "network" in node
            assert "active_seconds" in node

    def test_ranking_with_limit(self, populated_db):
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.node_activity_ranking(limit=1)
        assert len(result["nodes"]) == 1

    def test_ranking_no_history(self):
        analytics = HistoricalAnalytics(node_history=None)
        result = analytics.node_activity_ranking()
        assert "error" in result

    def test_node_a_and_b_have_more_observations(self, populated_db):
        """Nodes A and B have 24 hours of data; C has only 12."""
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.node_activity_ranking()

        nodes_by_id = {n["node_id"]: n for n in result["nodes"]}
        assert nodes_by_id["!node_a"]["observation_count"] > nodes_by_id["!node_c"]["observation_count"]
        assert nodes_by_id["!node_b"]["observation_count"] > nodes_by_id["!node_c"]["observation_count"]


# ---------------------------------------------------------------------------
# Network summary
# ---------------------------------------------------------------------------


class TestNetworkSummary:
    def test_basic_summary(self, populated_db):
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.network_summary()

        assert "unique_nodes" in result
        assert "total_observations" in result
        assert "avg_observations_per_node" in result
        assert "networks" in result

    def test_summary_network_breakdown(self, populated_db):
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.network_summary()

        networks = result["networks"]
        assert "meshtastic" in networks
        assert "reticulum" in networks
        assert networks["meshtastic"]["node_count"] == 2
        assert networks["reticulum"]["node_count"] == 1

    def test_summary_node_count(self, populated_db):
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.network_summary()
        assert result["unique_nodes"] == 3

    def test_summary_avg_observations(self, populated_db):
        analytics = HistoricalAnalytics(node_history=populated_db)
        result = analytics.network_summary()
        assert result["avg_observations_per_node"] > 0

    def test_summary_no_history(self):
        analytics = HistoricalAnalytics(node_history=None)
        result = analytics.network_summary()
        assert "error" in result

    def test_summary_custom_since(self, populated_db):
        now = int(time.time())
        analytics = HistoricalAnalytics(node_history=populated_db)
        # Only last hour -- should see fewer nodes
        result = analytics.network_summary(since=now - 3600)
        assert result["since"] == now - 3600


# ---------------------------------------------------------------------------
# Alert trends
# ---------------------------------------------------------------------------


class TestAlertTrends:
    def test_basic_trends(self, alert_engine):
        analytics = HistoricalAnalytics(alert_engine=alert_engine)
        result = analytics.alert_trends()

        assert "buckets" in result
        assert "total_alerts" in result
        assert result["total_alerts"] > 0

    def test_trend_buckets_have_severity_counts(self, alert_engine):
        analytics = HistoricalAnalytics(alert_engine=alert_engine)
        result = analytics.alert_trends()

        for bucket in result["buckets"]:
            assert "timestamp" in bucket
            assert "total" in bucket
            assert "critical" in bucket
            assert "warning" in bucket
            assert "info" in bucket

    def test_trends_no_engine(self):
        analytics = HistoricalAnalytics(alert_engine=None)
        result = analytics.alert_trends()
        assert "error" in result

    def test_trends_empty_engine(self):
        engine = AlertEngine()
        analytics = HistoricalAnalytics(alert_engine=engine)
        result = analytics.alert_trends()
        assert result["total_alerts"] == 0
        assert result["buckets"] == []

    def test_trends_custom_bucket_size(self, alert_engine):
        analytics = HistoricalAnalytics(alert_engine=alert_engine)
        result = analytics.alert_trends(bucket_seconds=1800)
        assert result["bucket_seconds"] == 1800

    def test_trends_total_matches_alerts(self, alert_engine):
        analytics = HistoricalAnalytics(alert_engine=alert_engine)
        result = analytics.alert_trends()

        bucket_total = sum(b["total"] for b in result["buckets"])
        assert bucket_total == result["total_alerts"]


# ---------------------------------------------------------------------------
# Integration: analytics with both history and alerts
# ---------------------------------------------------------------------------


class TestAnalyticsIntegration:
    def test_full_analytics_suite(self, populated_db, alert_engine):
        analytics = HistoricalAnalytics(
            node_history=populated_db,
            alert_engine=alert_engine,
        )

        growth = analytics.network_growth()
        assert len(growth["buckets"]) > 0

        heatmap = analytics.activity_heatmap()
        assert len(heatmap["hours"]) == 24

        ranking = analytics.node_activity_ranking()
        assert len(ranking["nodes"]) > 0

        summary = analytics.network_summary()
        assert summary["unique_nodes"] > 0

        trends = analytics.alert_trends()
        assert trends["total_alerts"] > 0

    def test_analytics_with_only_history(self, populated_db):
        """Analytics works with history but no alert engine."""
        analytics = HistoricalAnalytics(node_history=populated_db)

        growth = analytics.network_growth()
        assert len(growth["buckets"]) > 0

        trends = analytics.alert_trends()
        assert "error" in trends

    def test_analytics_with_only_alerts(self, alert_engine):
        """Analytics works with alert engine but no history."""
        analytics = HistoricalAnalytics(alert_engine=alert_engine)

        growth = analytics.network_growth()
        assert "error" in growth

        trends = analytics.alert_trends()
        assert trends["total_alerts"] > 0
