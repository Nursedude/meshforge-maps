"""Unit tests for src/utils/rate_limiter.py."""

import pytest

from src.utils.rate_limiter import RateLimiter


class TestRateLimiterBucket:
    def test_first_request_allowed(self):
        rl = RateLimiter(requests_per_minute=60)
        allowed, retry_after = rl.allow("203.0.113.1")
        assert allowed is True
        assert retry_after == 0

    def test_exhausts_then_denies(self):
        rl = RateLimiter(requests_per_minute=5)
        for _ in range(5):
            assert rl.allow("203.0.113.2")[0] is True
        allowed, retry_after = rl.allow("203.0.113.2")
        assert allowed is False
        assert retry_after >= 1

    def test_per_ip_isolation(self):
        rl = RateLimiter(requests_per_minute=2)
        assert rl.allow("203.0.113.3")[0] is True
        assert rl.allow("203.0.113.3")[0] is True
        # Same IP is now denied
        assert rl.allow("203.0.113.3")[0] is False
        # Different IP still has full bucket
        assert rl.allow("203.0.113.4")[0] is True
        assert rl.allow("203.0.113.4")[0] is True

    def test_invalid_rate_rejected(self):
        with pytest.raises(ValueError):
            RateLimiter(requests_per_minute=0)
        with pytest.raises(ValueError):
            RateLimiter(requests_per_minute=-1)

    def test_refill_over_time(self, monkeypatch):
        """After enough wall-time, an exhausted bucket should refill."""
        # Use monotonic-mock pattern: advance the clock manually.
        fake_now = [1000.0]

        def fake_monotonic():
            return fake_now[0]

        monkeypatch.setattr("src.utils.rate_limiter.time.monotonic", fake_monotonic)
        rl = RateLimiter(requests_per_minute=60)  # 1 token / sec
        for _ in range(60):
            rl.allow("203.0.113.5")
        assert rl.allow("203.0.113.5")[0] is False
        fake_now[0] += 2.0  # 2 tokens worth of refill
        assert rl.allow("203.0.113.5")[0] is True


class TestRateLimiterPruning:
    def test_idle_buckets_are_pruned(self, monkeypatch):
        fake_now = [1000.0]

        def fake_monotonic():
            return fake_now[0]

        monkeypatch.setattr("src.utils.rate_limiter.time.monotonic", fake_monotonic)
        rl = RateLimiter(requests_per_minute=60)
        for i in range(10):
            rl.allow(f"203.0.113.{10 + i}")
        assert rl.bucket_count == 10
        # Jump past the prune window then make one request to trigger sweep.
        fake_now[0] += RateLimiter._PRUNE_IDLE_SECONDS + 5
        rl.allow("203.0.113.99")
        # Old buckets pruned; only the fresh one remains.
        assert rl.bucket_count == 1
