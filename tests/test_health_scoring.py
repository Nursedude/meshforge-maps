"""Tests for per-node health scoring."""

import time

import pytest

from src.utils.health_scoring import (
    BATTERY_FULL,
    BATTERY_LOW,
    CHANNEL_UTIL_HIGH,
    CHANNEL_UTIL_LOW,
    FRESH_THRESHOLD,
    FRESHNESS_THRESHOLDS,
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
    NodeHealthScorer,
)


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

    def test_no_data_unknown_scores_fair(self, scorer):
        """Node with no data and unknown online status scores 'fair' (50), not critical."""
        result = scorer.score_node("n1", {}, now=1000.0)
        assert result.score == 50
        assert result.status == "fair"
        assert result.available_weight == 0
        assert result.components == {}

    def test_online_node_no_telemetry_scores_good(self, scorer):
        """AREDN-style node with is_online=True but no telemetry gets 'good'."""
        result = scorer.score_node("aredn1", {"is_online": True, "network": "aredn"}, now=1000.0)
        assert result.score == 70
        assert result.status == "good"
        assert result.available_weight == 0

    def test_offline_node_no_telemetry_scores_critical(self, scorer):
        """Offline node with no telemetry gets 'critical'."""
        result = scorer.score_node("aredn2", {"is_online": False, "network": "aredn"}, now=1000.0)
        assert result.score == 15
        assert result.status == "critical"
        assert result.available_weight == 0

    def test_online_node_stale_freshness_floors_to_good(self, scorer):
        """Online node with only stale last_seen should floor at 60 ('good')."""
        # last_seen 50 min ago — stale enough for low freshness, but online
        now = 10000.0
        result = scorer.score_node(
            "aredn3",
            {"is_online": True, "last_seen": now - 3000, "network": "aredn"},
            now=now,
        )
        assert result.score >= 60
        assert result.status == "good"

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
        # At capacity (3), adding n4 should evict n1 (least recently accessed)
        scorer.score_node("n4", {"battery": 80}, now=4000.0)
        assert scorer.get_node_score("n1") is None
        assert scorer.get_node_score("n4") is not None
        assert scorer.scored_node_count == 3

    def test_eviction_lru_access_preserves_node(self, scorer):
        """Accessing a node via get_node_score updates its LRU timestamp,
        preventing eviction even if it was scored earliest."""
        scorer.score_node("n1", {"battery": 80}, now=1000.0)
        scorer.score_node("n2", {"battery": 80}, now=2000.0)
        scorer.score_node("n3", {"battery": 80}, now=3000.0)
        # Access n1 — this should update its last_accessed time
        scorer.get_node_score("n1")
        # Adding n4 at capacity should evict n2 (least recently accessed), not n1
        scorer.score_node("n4", {"battery": 80}, now=5000.0)
        assert scorer.get_node_score("n1") is not None  # preserved by access
        assert scorer.get_node_score("n2") is None  # evicted (least recently accessed)
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


class TestOnlineBoostRegression:
    """Regression tests for online boost covering freshness + reliability."""

    @pytest.fixture
    def scorer(self):
        return NodeHealthScorer()

    def test_online_node_freshness_plus_reliability_floors_to_good(self, scorer):
        """Online node with stale freshness AND connectivity_state should score >= 60.

        When NodeStateTracker provides connectivity_state, both freshness and
        reliability get scored (available=35).  The online boost must cover this
        case so the node isn't marked critical.
        """
        now = time.time()
        props = {
            "is_online": True,
            "last_seen": now - 7200,        # stale (2h ago)
            "connectivity_state": "new",    # triggers reliability scoring
        }
        result = scorer.score_node("!aabb0011", props, now=now)
        assert result.score >= 60, (
            f"Online node with freshness+reliability only scored {result.score}, expected >= 60"
        )
        assert result.status != "critical"

    def test_aredn_node_with_last_seen_not_critical(self, scorer):
        """AREDN node with is_online=True and last_seen should not be critical."""
        now = time.time()
        props = {
            "is_online": True,
            "last_seen": now,
            "network": "aredn",
        }
        result = scorer.score_node("WH6DPT-6", props, now=now)
        assert result.score >= 60, (
            f"AREDN online node scored {result.score}, expected >= 60"
        )
        assert result.status != "critical"

    def test_meshcore_style_node_not_critical(self, scorer):
        """MeshCore-style node with only last_seen + is_online should not be critical.

        Simulates a MeshCore node that reports last_advert and is_online=True
        plus a connectivity_state from NodeStateTracker.
        """
        now = time.time()
        props = {
            "is_online": True,
            "last_seen": now - 1800,         # 30 min ago, within meshcore threshold
            "connectivity_state": "new",
            "network": "meshcore",
        }
        result = scorer.score_node("abc123", props, now=now)
        assert result.score >= 60, (
            f"MeshCore online node scored {result.score}, expected >= 60"
        )
        assert result.status != "critical"


class TestPerNetworkFreshness:
    """Tests for per-network freshness thresholds."""

    @pytest.fixture
    def scorer(self):
        return NodeHealthScorer()

    def test_meshmap_node_2h_ago_not_stale(self, scorer):
        """meshmap.net node seen 2h ago should score well — its stale threshold is 4h."""
        now = 10000.0
        props = {
            "is_online": True,
            "last_seen": now - 7200,  # 2h ago
            "network": "meshmap",
        }
        result = scorer.score_node("mesh1", props, now=now)
        fresh = result.components["freshness"]["score"]
        # 2h is halfway between fresh (1h) and stale (4h), so ~10/20
        assert fresh > 5, f"meshmap node 2h ago got freshness {fresh}, expected > 5"
        assert result.score >= 60

    def test_aredn_node_1h_ago_scores_well(self, scorer):
        """AREDN node seen 1h ago should score high freshness — fresh threshold is 30min."""
        now = 10000.0
        props = {
            "is_online": True,
            "last_seen": now - 3600,  # 1h ago
            "network": "aredn",
        }
        result = scorer.score_node("aredn1", props, now=now)
        fresh = result.components["freshness"]["score"]
        # 1h into a (30min, 2h) window = ~63% → ~12.6/20
        assert fresh > 8, f"AREDN node 1h ago got freshness {fresh}, expected > 8"

    def test_aredn_worldmap_source_uses_aredn_thresholds(self, scorer):
        """source='aredn_worldmap' should map to AREDN freshness thresholds."""
        now = 10000.0
        props = {
            "is_online": True,
            "last_seen": now - 3600,
            "source": "aredn_worldmap",
        }
        result = scorer.score_node("aw1", props, now=now)
        fresh = result.components["freshness"]["score"]
        assert fresh > 8

    def test_meshmap_net_source_uses_meshmap_thresholds(self, scorer):
        """source='meshmap.net' should map to meshmap freshness thresholds."""
        now = 10000.0
        props = {
            "is_online": True,
            "last_seen": now - 7200,
            "source": "meshmap.net",
        }
        result = scorer.score_node("mm1", props, now=now)
        fresh = result.components["freshness"]["score"]
        assert fresh > 5

    def test_meshtastic_unchanged(self, scorer):
        """Meshtastic nodes still use 5min/1h thresholds."""
        now = 10000.0
        props = {
            "last_seen": now - 600,  # 10min ago
            "network": "meshtastic",
        }
        result = scorer.score_node("mt1", props, now=now)
        fresh = result.components["freshness"]["score"]
        # 10min into (5min, 1h) = ~91% → ~18/20
        assert fresh > 15

    def test_unknown_network_uses_defaults(self, scorer):
        """Unknown network falls back to default (Meshtastic) thresholds."""
        now = 10000.0
        props = {"last_seen": now - 600, "network": "unknown_net"}
        result = scorer.score_node("u1", props, now=now)
        fresh = result.components["freshness"]["score"]
        assert fresh > 15  # Same as meshtastic for 10min age


class TestOnlineFloorWidened:
    """Tests for the widened is_online floor logic."""

    @pytest.fixture
    def scorer(self):
        return NodeHealthScorer()

    def test_online_node_with_battery_and_stale_freshness_floors(self, scorer):
        """Online node with battery + stale freshness should floor at 60.

        Previously this bypassed the floor because available > 35.
        """
        now = 10000.0
        props = {
            "is_online": True,
            "battery": 80,
            "last_seen": now - STALE_THRESHOLD * 2,  # very stale
        }
        result = scorer.score_node("n1", props, now=now)
        assert result.score >= 60, (
            f"Online node with battery+stale freshness scored {result.score}"
        )

    def test_online_node_with_congestion_and_stale_freshness_floors(self, scorer):
        """Online node with congestion + stale freshness should floor at 60."""
        now = 10000.0
        props = {
            "is_online": True,
            "channel_util": 10.0,
            "last_seen": now - STALE_THRESHOLD * 2,
        }
        result = scorer.score_node("n1", props, now=now)
        assert result.score >= 60

    def test_rich_telemetry_bypasses_floor(self, scorer):
        """Node with 2+ rich metrics and bad values should NOT be floored."""
        now = 10000.0
        props = {
            "is_online": True,
            "battery": 0,           # critical battery
            "channel_util": 100.0,  # severe congestion
            "last_seen": now - STALE_THRESHOLD * 2,
        }
        result = scorer.score_node("n1", props, now=now)
        # Has battery + congestion (2 rich metrics) → floor bypassed
        assert result.score < 60, (
            f"Node with bad rich telemetry scored {result.score}, expected < 60"
        )

    def test_unknown_online_status_scores_fair(self, scorer):
        """Node with is_online=None and no telemetry gets 50 (fair)."""
        result = scorer.score_node("n1", {"is_online": None}, now=1000.0)
        assert result.score == 50
        assert result.status == "fair"
