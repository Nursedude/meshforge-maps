"""Tests for per-node health scoring."""

import time

import pytest

from src.utils.health_scoring import (
    BATTERY_FULL,
    BATTERY_LOW,
    CHANNEL_UTIL_HIGH,
    CHANNEL_UTIL_LOW,
    FRESH_THRESHOLD,
    MAX_HOPS_SCORED,
    SNR_EXCELLENT,
    SNR_POOR,
    STALE_THRESHOLD,
    VOLTAGE_HEALTHY,
    VOLTAGE_MIN,
    WEIGHT_BATTERY,
    WEIGHT_CONGESTION,
    WEIGHT_FRESHNESS,
    WEIGHT_RELIABILITY,
    WEIGHT_SIGNAL,
    NodeHealthScore,
    NodeHealthScorer,
    _clamp,
    _linear_score,
    _score_label,
)


class TestHelpers:
    """Tests for module-level helper functions."""

    @pytest.mark.parametrize("score,expected", [
        (90, "excellent"), (80, "excellent"),
        (60, "good"), (79, "good"),
        (40, "fair"), (59, "fair"),
        (20, "poor"), (39, "poor"),
        (0, "critical"), (19, "critical"),
    ])
    def test_score_label(self, score, expected):
        assert _score_label(score) == expected

    @pytest.mark.parametrize("val,lo,hi,expected", [
        (5.0, 0.0, 10.0, 5.0),
        (-1.0, 0.0, 10.0, 0.0),
        (15.0, 0.0, 10.0, 10.0),
    ])
    def test_clamp(self, val, lo, hi, expected):
        assert _clamp(val, lo, hi) == expected

    @pytest.mark.parametrize("value,bad,good,max_pts,expected", [
        (50, 0, 100, 10.0, 5.0),    # midpoint
        (100, 0, 100, 10.0, 10.0),   # at good
        (0, 0, 100, 10.0, 0.0),      # at bad
        (-10, 0, 100, 10.0, 0.0),    # below bad
        (150, 0, 100, 10.0, 10.0),   # above good
        (50, 50, 50, 10.0, 10.0),    # equal bounds, at value
        (49, 50, 50, 10.0, 0.0),     # equal bounds, below
    ])
    def test_linear_score(self, value, bad, good, max_pts, expected):
        assert _linear_score(value, bad, good, max_pts) == pytest.approx(expected)


class TestNodeHealthScore:
    """Tests for NodeHealthScore data class."""

    def test_to_dict(self):
        score = NodeHealthScore(
            node_id="!aabbccdd",
            score=75,
            status="good",
            components={"battery": {"score": 20.0, "max": 25}},
            available_weight=25,
            timestamp=1000.0,
        )
        d = score.to_dict()
        assert d["node_id"] == "!aabbccdd"
        assert d["score"] == 75
        assert d["status"] == "good"
        assert d["components"]["battery"]["score"] == 20.0
        assert d["available_weight"] == 25
        assert d["timestamp"] == 1000.0


class TestBatteryScoring:
    """Tests for battery component scoring."""

    @pytest.fixture
    def scorer(self):
        return NodeHealthScorer()

    def test_full_battery(self, scorer):
        result = scorer.score_node("n1", {"battery": 100}, now=1000.0)
        assert "battery" in result.components
        assert result.components["battery"]["score"] == WEIGHT_BATTERY

    def test_low_battery(self, scorer):
        result = scorer.score_node("n1", {"battery": BATTERY_LOW}, now=1000.0)
        assert result.components["battery"]["score"] == pytest.approx(0.0, abs=0.1)

    def test_dead_battery(self, scorer):
        result = scorer.score_node("n1", {"battery": 0}, now=1000.0)
        assert result.components["battery"]["score"] == pytest.approx(0.0, abs=0.1)

    def test_mid_battery(self, scorer):
        mid = (BATTERY_LOW + BATTERY_FULL) / 2
        result = scorer.score_node("n1", {"battery": int(mid)}, now=1000.0)
        batt = result.components["battery"]["score"]
        assert 5 < batt < 20  # Roughly mid-range

    def test_voltage_only(self, scorer):
        result = scorer.score_node("n1", {"voltage": VOLTAGE_HEALTHY}, now=1000.0)
        assert "battery" in result.components
        assert result.components["battery"]["score"] == pytest.approx(WEIGHT_BATTERY, abs=0.1)

    def test_voltage_critical(self, scorer):
        result = scorer.score_node("n1", {"voltage": VOLTAGE_MIN}, now=1000.0)
        assert result.components["battery"]["score"] == pytest.approx(0.0, abs=0.1)

    def test_battery_and_voltage(self, scorer):
        result = scorer.score_node(
            "n1", {"battery": 100, "voltage": VOLTAGE_HEALTHY}, now=1000.0
        )
        assert "battery" in result.components
        detail = result.components["battery"]
        assert "battery_level" in detail
        assert "voltage" in detail
        assert detail["score"] == pytest.approx(WEIGHT_BATTERY, abs=0.1)

    def test_no_battery_data(self, scorer):
        result = scorer.score_node("n1", {"snr": 10.0}, now=1000.0)
        assert "battery" not in result.components

    def test_invalid_battery_type(self, scorer):
        result = scorer.score_node("n1", {"battery": "bad"}, now=1000.0)
        assert "battery" not in result.components

    def test_invalid_voltage_type(self, scorer):
        result = scorer.score_node("n1", {"voltage": "bad"}, now=1000.0)
        assert "battery" not in result.components


class TestSignalScoring:
    """Tests for signal component scoring."""

    @pytest.fixture
    def scorer(self):
        return NodeHealthScorer()

    def test_excellent_snr(self, scorer):
        result = scorer.score_node("n1", {"snr": SNR_EXCELLENT + 5}, now=1000.0)
        assert result.components["signal"]["score"] == pytest.approx(WEIGHT_SIGNAL, abs=0.1)

    def test_poor_snr(self, scorer):
        result = scorer.score_node("n1", {"snr": SNR_POOR}, now=1000.0)
        assert result.components["signal"]["score"] == pytest.approx(0.0, abs=0.1)

    def test_marginal_snr(self, scorer):
        result = scorer.score_node("n1", {"snr": 0.0}, now=1000.0)
        sig = result.components["signal"]["score"]
        assert 5 < sig < 20

    def test_hops_zero(self, scorer):
        result = scorer.score_node("n1", {"hops_away": 0}, now=1000.0)
        assert result.components["signal"]["score"] == pytest.approx(WEIGHT_SIGNAL, abs=0.1)

    def test_hops_max(self, scorer):
        result = scorer.score_node("n1", {"hops_away": MAX_HOPS_SCORED}, now=1000.0)
        assert result.components["signal"]["score"] == pytest.approx(0.0, abs=0.1)

    def test_snr_and_hops(self, scorer):
        result = scorer.score_node(
            "n1", {"snr": SNR_EXCELLENT, "hops_away": 0}, now=1000.0
        )
        detail = result.components["signal"]
        assert "snr" in detail
        assert "hops_away" in detail
        assert detail["score"] == pytest.approx(WEIGHT_SIGNAL, abs=0.1)

    def test_no_signal_data(self, scorer):
        result = scorer.score_node("n1", {"battery": 80}, now=1000.0)
        assert "signal" not in result.components

    def test_invalid_snr_type(self, scorer):
        result = scorer.score_node("n1", {"snr": "bad"}, now=1000.0)
        assert "signal" not in result.components

    def test_negative_hops_clamped(self, scorer):
        result = scorer.score_node("n1", {"hops_away": -1}, now=1000.0)
        assert "signal" in result.components


class TestFreshnessScoring:
    """Tests for freshness component scoring."""

    @pytest.fixture
    def scorer(self):
        return NodeHealthScorer()

    def test_just_seen(self, scorer):
        now = 10000.0
        result = scorer.score_node("n1", {"last_seen": now - 10}, now=now)
        assert result.components["freshness"]["score"] == pytest.approx(
            WEIGHT_FRESHNESS, abs=0.5
        )

    def test_stale(self, scorer):
        now = 10000.0
        result = scorer.score_node("n1", {"last_seen": now - STALE_THRESHOLD}, now=now)
        assert result.components["freshness"]["score"] == pytest.approx(0.0, abs=0.5)

    def test_mid_freshness(self, scorer):
        now = 10000.0
        mid_age = (FRESH_THRESHOLD + STALE_THRESHOLD) / 2
        result = scorer.score_node("n1", {"last_seen": now - mid_age}, now=now)
        fresh = result.components["freshness"]["score"]
        assert 3 < fresh < 17

    def test_future_timestamp_clamped(self, scorer):
        now = 10000.0
        result = scorer.score_node("n1", {"last_seen": now + 100}, now=now)
        # Clock skew protection: age clamped to 0 = fully fresh
        assert result.components["freshness"]["score"] == pytest.approx(
            WEIGHT_FRESHNESS, abs=0.5
        )

    def test_no_last_seen(self, scorer):
        result = scorer.score_node("n1", {"battery": 80}, now=1000.0)
        assert "freshness" not in result.components

    def test_invalid_last_seen_type(self, scorer):
        result = scorer.score_node("n1", {"last_seen": "bad"}, now=1000.0)
        assert "freshness" not in result.components


class TestReliabilityScoring:
    """Tests for reliability component scoring."""

    @pytest.fixture
    def scorer(self):
        return NodeHealthScorer()

    def test_stable_state(self, scorer):
        result = scorer.score_node("n1", {}, connectivity_state="stable", now=1000.0)
        assert result.components["reliability"]["score"] == pytest.approx(
            WEIGHT_RELIABILITY, abs=0.1
        )

    def test_new_state(self, scorer):
        result = scorer.score_node("n1", {}, connectivity_state="new", now=1000.0)
        expected = WEIGHT_RELIABILITY * 0.7
        assert result.components["reliability"]["score"] == pytest.approx(expected, abs=0.1)

    def test_intermittent_state(self, scorer):
        result = scorer.score_node("n1", {}, connectivity_state="intermittent", now=1000.0)
        expected = WEIGHT_RELIABILITY * 0.3
        assert result.components["reliability"]["score"] == pytest.approx(expected, abs=0.1)

    def test_offline_state(self, scorer):
        result = scorer.score_node("n1", {}, connectivity_state="offline", now=1000.0)
        assert result.components["reliability"]["score"] == pytest.approx(0.0, abs=0.1)

    def test_no_state(self, scorer):
        result = scorer.score_node("n1", {"battery": 80}, now=1000.0)
        assert "reliability" not in result.components

    def test_unknown_state_fallback(self, scorer):
        result = scorer.score_node("n1", {}, connectivity_state="unknown", now=1000.0)
        # Unknown state gets 50% of max
        expected = WEIGHT_RELIABILITY * 0.5
        assert result.components["reliability"]["score"] == pytest.approx(expected, abs=0.1)


class TestCongestionScoring:
    """Tests for congestion component scoring."""

    @pytest.fixture
    def scorer(self):
        return NodeHealthScorer()

    def test_no_congestion(self, scorer):
        result = scorer.score_node("n1", {"channel_util": 0.0}, now=1000.0)
        assert result.components["congestion"]["score"] == pytest.approx(
            WEIGHT_CONGESTION, abs=0.1
        )

    def test_severe_congestion(self, scorer):
        result = scorer.score_node("n1", {"channel_util": CHANNEL_UTIL_HIGH}, now=1000.0)
        assert result.components["congestion"]["score"] == pytest.approx(0.0, abs=0.1)

    def test_mid_congestion(self, scorer):
        mid = (CHANNEL_UTIL_LOW + CHANNEL_UTIL_HIGH) / 2
        result = scorer.score_node("n1", {"channel_util": mid}, now=1000.0)
        cong = result.components["congestion"]["score"]
        assert 3 < cong < 12

    def test_air_util_tx_only(self, scorer):
        result = scorer.score_node("n1", {"air_util_tx": 0.0}, now=1000.0)
        assert result.components["congestion"]["score"] == pytest.approx(
            WEIGHT_CONGESTION, abs=0.1
        )

    def test_both_util_metrics(self, scorer):
        result = scorer.score_node(
            "n1", {"channel_util": 10.0, "air_util_tx": 10.0}, now=1000.0
        )
        detail = result.components["congestion"]
        assert "channel_util" in detail
        assert "air_util_tx" in detail
        assert detail["score"] == pytest.approx(WEIGHT_CONGESTION, abs=0.1)

    def test_no_congestion_data(self, scorer):
        result = scorer.score_node("n1", {"battery": 80}, now=1000.0)
        assert "congestion" not in result.components


class TestCompositeScoring:
    """Tests for composite score normalization."""

    @pytest.fixture
    def scorer(self):
        return NodeHealthScorer()

    def test_perfect_node(self, scorer):
        props = {
            "battery": 100,
            "voltage": VOLTAGE_HEALTHY,
            "snr": SNR_EXCELLENT + 2,
            "hops_away": 0,
            "last_seen": 999.0,
            "channel_util": 0.0,
            "air_util_tx": 0.0,
        }
        result = scorer.score_node("n1", props, connectivity_state="stable", now=1000.0)
        assert result.score >= 90
        assert result.status == "excellent"
        assert result.available_weight == 100  # All components

    def test_critical_node(self, scorer):
        now = 10000.0
        props = {
            "battery": 0,
            "snr": SNR_POOR - 5,
            "last_seen": now - STALE_THRESHOLD * 2,
            "channel_util": 100.0,
        }
        result = scorer.score_node("n1", props, connectivity_state="offline", now=now)
        assert result.score <= 10
        assert result.status == "critical"

    def test_partial_data_normalization(self, scorer):
        """Node with only battery data should still get 0-100 score."""
        result = scorer.score_node("n1", {"battery": 100}, now=1000.0)
        # Only battery available (25 weight), should normalize to ~100
        assert result.score >= 90
        assert result.available_weight == WEIGHT_BATTERY

    def test_no_data_scores_zero(self, scorer):
        result = scorer.score_node("n1", {}, now=1000.0)
        assert result.score == 0
        assert result.available_weight == 0
        assert result.components == {}

    def test_score_clamped_to_100(self, scorer):
        result = scorer.score_node("n1", {"battery": 200}, now=1000.0)
        assert result.score <= 100

    def test_score_clamped_to_0(self, scorer):
        result = scorer.score_node("n1", {"battery": -100}, now=1000.0)
        assert result.score >= 0


class TestScorerCache:
    """Tests for scorer caching and eviction."""

    @pytest.fixture
    def scorer(self):
        return NodeHealthScorer(max_nodes=3)

    def test_cache_stores_score(self, scorer):
        scorer.score_node("n1", {"battery": 80}, now=1000.0)
        cached = scorer.get_node_score("n1")
        assert cached is not None
        assert cached["node_id"] == "n1"

    def test_cache_miss(self, scorer):
        assert scorer.get_node_score("nonexistent") is None

    def test_cache_updates_on_rescore(self, scorer):
        scorer.score_node("n1", {"battery": 80}, now=1000.0)
        scorer.score_node("n1", {"battery": 20}, now=2000.0)
        cached = scorer.get_node_score("n1")
        assert cached["timestamp"] == 2000.0

    def test_eviction_at_capacity(self, scorer):
        scorer.score_node("n1", {"battery": 80}, now=1000.0)
        scorer.score_node("n2", {"battery": 80}, now=2000.0)
        scorer.score_node("n3", {"battery": 80}, now=3000.0)
        # At capacity (3), adding n4 should evict n1 (oldest)
        scorer.score_node("n4", {"battery": 80}, now=4000.0)
        assert scorer.get_node_score("n1") is None
        assert scorer.get_node_score("n4") is not None
        assert scorer.scored_node_count == 3

    def test_remove_node(self, scorer):
        scorer.score_node("n1", {"battery": 80}, now=1000.0)
        scorer.remove_node("n1")
        assert scorer.get_node_score("n1") is None
        assert scorer.scored_node_count == 0

    def test_remove_nonexistent_node(self, scorer):
        # Should not raise
        scorer.remove_node("nonexistent")

    def test_get_all_scores(self, scorer):
        scorer.score_node("n1", {"battery": 80}, now=1000.0)
        scorer.score_node("n2", {"battery": 40}, now=1000.0)
        all_scores = scorer.get_all_scores()
        assert "n1" in all_scores
        assert "n2" in all_scores
        assert all_scores["n1"] > all_scores["n2"]


class TestScorerSummary:
    """Tests for summary statistics."""

    def test_empty_summary(self):
        scorer = NodeHealthScorer()
        s = scorer.get_summary()
        assert s["scored_nodes"] == 0
        assert s["average_score"] == 0

    def test_summary_with_nodes(self):
        scorer = NodeHealthScorer()
        scorer.score_node("n1", {"battery": 100}, now=1000.0)
        scorer.score_node("n2", {"battery": 50}, now=1000.0)
        s = scorer.get_summary()
        assert s["scored_nodes"] == 2
        assert s["average_score"] > 0
        assert "min_score" in s
        assert "max_score" in s
        assert s["max_score"] >= s["min_score"]
        assert "status_counts" in s
        assert "component_averages" in s
        assert "battery" in s["component_averages"]

    def test_summary_status_counts(self):
        scorer = NodeHealthScorer()
        scorer.score_node("n1", {"battery": 100}, now=1000.0)
        scorer.score_node("n2", {"battery": 0}, now=1000.0)
        s = scorer.get_summary()
        total = sum(s["status_counts"].values())
        assert total == 2
