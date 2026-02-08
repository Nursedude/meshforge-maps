"""Tests for reconnect strategy: exponential backoff with jitter."""

import time

import pytest

from src.utils.reconnect import ReconnectStrategy


class TestReconnectStrategyBasics:
    """Tests for basic backoff calculation."""

    def test_first_delay_is_base(self):
        strategy = ReconnectStrategy(base_delay=2.0, jitter_factor=0.0)
        delay = strategy.next_delay()
        assert delay == 2.0

    def test_delays_increase_exponentially(self):
        strategy = ReconnectStrategy(
            base_delay=1.0, multiplier=2.0, max_delay=100.0, jitter_factor=0.0
        )
        d1 = strategy.next_delay()
        d2 = strategy.next_delay()
        d3 = strategy.next_delay()
        assert d1 == 1.0   # 1 * 2^0
        assert d2 == 2.0   # 1 * 2^1
        assert d3 == 4.0   # 1 * 2^2

    def test_delay_capped_at_max(self):
        strategy = ReconnectStrategy(
            base_delay=1.0, multiplier=2.0, max_delay=5.0, jitter_factor=0.0
        )
        delays = [strategy.next_delay() for _ in range(10)]
        assert all(d <= 5.0 for d in delays)

    def test_jitter_adds_randomness(self):
        strategy = ReconnectStrategy(
            base_delay=10.0, jitter_factor=0.25, max_delay=100.0
        )
        delays = [strategy.next_delay() for _ in range(20)]
        # Reset and get same sequence -- should differ due to jitter
        strategy.reset()
        delays2 = [strategy.next_delay() for _ in range(20)]
        # Extremely unlikely all 20 are identical with jitter
        assert delays != delays2

    def test_jitter_within_bounds(self):
        strategy = ReconnectStrategy(
            base_delay=10.0, jitter_factor=0.25, max_delay=100.0
        )
        # First delay should be 10.0 + uniform(0, 2.5)
        delay = strategy.next_delay()
        assert 10.0 <= delay <= 12.5

    def test_zero_jitter_is_deterministic(self):
        s1 = ReconnectStrategy(base_delay=3.0, jitter_factor=0.0)
        s2 = ReconnectStrategy(base_delay=3.0, jitter_factor=0.0)
        for _ in range(5):
            assert s1.next_delay() == s2.next_delay()


class TestReconnectStrategyAttempts:
    """Tests for attempt counting and retry limits."""

    def test_attempt_starts_at_zero(self):
        strategy = ReconnectStrategy()
        assert strategy.attempt == 0

    def test_attempt_increments_on_next_delay(self):
        strategy = ReconnectStrategy()
        strategy.next_delay()
        assert strategy.attempt == 1
        strategy.next_delay()
        assert strategy.attempt == 2

    def test_reset_clears_attempt(self):
        strategy = ReconnectStrategy()
        strategy.next_delay()
        strategy.next_delay()
        strategy.reset()
        assert strategy.attempt == 0

    def test_total_attempts_persist_across_resets(self):
        strategy = ReconnectStrategy()
        strategy.next_delay()
        strategy.next_delay()
        strategy.reset()
        strategy.next_delay()
        assert strategy.total_attempts == 3
        assert strategy.attempt == 1

    def test_should_retry_unlimited(self):
        strategy = ReconnectStrategy(max_retries=None)
        for _ in range(100):
            assert strategy.should_retry() is True
            strategy.next_delay()

    def test_should_retry_limited(self):
        strategy = ReconnectStrategy(max_retries=3)
        assert strategy.should_retry() is True
        strategy.next_delay()
        assert strategy.should_retry() is True
        strategy.next_delay()
        assert strategy.should_retry() is True
        strategy.next_delay()
        assert strategy.should_retry() is False

    def test_should_retry_resets_with_strategy(self):
        strategy = ReconnectStrategy(max_retries=2)
        strategy.next_delay()
        strategy.next_delay()
        assert strategy.should_retry() is False
        strategy.reset()
        assert strategy.should_retry() is True


class TestReconnectStrategyWait:
    """Tests for the wait() method."""

    def test_wait_sleeps_for_delay(self):
        strategy = ReconnectStrategy(base_delay=0.05, jitter_factor=0.0)
        start = time.time()
        delay = strategy.wait()
        elapsed = time.time() - start
        assert delay == pytest.approx(0.05, abs=0.01)
        assert elapsed >= 0.04


class TestReconnectStrategyFactories:
    """Tests for factory class methods."""

    def test_for_mqtt_is_unlimited(self):
        strategy = ReconnectStrategy.for_mqtt()
        assert strategy.should_retry() is True
        for _ in range(50):
            strategy.next_delay()
        assert strategy.should_retry() is True

    def test_for_mqtt_starts_at_two_seconds(self):
        strategy = ReconnectStrategy.for_mqtt()
        delay = strategy.next_delay()
        # 2.0 + jitter(0, 0.5)
        assert 2.0 <= delay <= 2.5

    def test_for_mqtt_max_delay_is_120(self):
        strategy = ReconnectStrategy.for_mqtt()
        # Exhaust to max
        for _ in range(20):
            delay = strategy.next_delay()
        # With jitter_factor=0.25, max is 120 + 30 = 150
        assert delay <= 150.0

    def test_for_collector_is_limited(self):
        strategy = ReconnectStrategy.for_collector()
        assert strategy.should_retry() is True
        for _ in range(3):
            strategy.next_delay()
        assert strategy.should_retry() is False

    def test_for_collector_starts_at_one_second(self):
        strategy = ReconnectStrategy.for_collector()
        delay = strategy.next_delay()
        # 1.0 + jitter(0, 0.15)
        assert 1.0 <= delay <= 1.15

    def test_for_collector_max_delay_is_ten(self):
        strategy = ReconnectStrategy.for_collector()
        for _ in range(10):
            delay = strategy.next_delay()
        # 10.0 + jitter(0, 1.5)
        assert delay <= 11.5
