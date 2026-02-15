"""Tests for circuit breaker pattern: CircuitBreaker and CircuitBreakerRegistry."""

import threading
import time

import pytest

from src.utils.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, CircuitState


class TestCircuitBreakerStates:
    """Tests for circuit breaker state transitions."""

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED

    def test_can_execute_when_closed(self):
        cb = CircuitBreaker("test")
        assert cb.can_execute() is True

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_opens_at_failure_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_cannot_execute_when_open(self):
        cb = CircuitBreaker("test", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.can_execute() is False

    def test_transitions_to_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

    def test_can_execute_when_half_open(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        time.sleep(0.15)
        assert cb.can_execute() is True

    def test_closes_on_success_from_half_open(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_reopens_on_failure_from_half_open(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        # Should not trip: 2 failures, then success reset, then 1 failure
        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerReset:
    """Tests for manual circuit breaker reset."""

    def test_reset_closes_open_circuit(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_reset_clears_failure_count(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.reset()
        cb.record_failure()
        # Only 1 failure after reset, should stay closed
        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerStats:
    """Tests for circuit breaker statistics."""

    def test_stats_after_mixed_operations(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        cb.record_success()
        cb.record_success()
        cb.record_failure()
        stats = cb.get_stats()
        assert stats["total_successes"] == 2
        assert stats["total_failures"] == 1
        assert stats["failure_count"] == 1
        assert stats["last_failure_time"] is not None

    def test_stats_tracks_rejected_requests(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60)
        cb.record_failure()
        cb.can_execute()  # Should be rejected
        cb.can_execute()  # Should be rejected
        stats = cb.get_stats()
        assert stats["total_rejected"] == 2



class TestCircuitBreakerThreadSafety:
    """Tests for thread safety of circuit breaker."""

    def test_concurrent_failures_dont_corrupt_state(self):
        cb = CircuitBreaker("test", failure_threshold=100)
        errors = []

        def record_many():
            try:
                for _ in range(50):
                    cb.record_failure()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        stats = cb.get_stats()
        assert stats["total_failures"] == 200

    def test_concurrent_success_and_failure(self):
        cb = CircuitBreaker("test", failure_threshold=1000)
        errors = []

        def do_successes():
            try:
                for _ in range(100):
                    cb.record_success()
            except Exception as e:
                errors.append(e)

        def do_failures():
            try:
                for _ in range(100):
                    cb.record_failure()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=do_successes)
        t2 = threading.Thread(target=do_failures)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0
        stats = cb.get_stats()
        assert stats["total_successes"] == 100
        assert stats["total_failures"] == 100


class TestCircuitBreakerRegistry:
    """Tests for CircuitBreakerRegistry."""

    def test_creates_breaker_on_first_get(self):
        registry = CircuitBreakerRegistry()
        cb = registry.get("meshtastic")
        assert cb.name == "meshtastic"
        assert cb.state == CircuitState.CLOSED

    def test_returns_same_breaker_on_second_get(self):
        registry = CircuitBreakerRegistry()
        cb1 = registry.get("meshtastic")
        cb2 = registry.get("meshtastic")
        assert cb1 is cb2

    def test_different_names_get_different_breakers(self):
        registry = CircuitBreakerRegistry()
        cb1 = registry.get("meshtastic")
        cb2 = registry.get("reticulum")
        assert cb1 is not cb2
        assert cb1.name == "meshtastic"
        assert cb2.name == "reticulum"

    def test_custom_thresholds_on_creation(self):
        registry = CircuitBreakerRegistry()
        cb = registry.get("custom", failure_threshold=10, recovery_timeout=120.0)
        stats = cb.get_stats()
        assert stats["failure_threshold"] == 10
        assert stats["recovery_timeout"] == 120.0

    def test_default_thresholds_from_registry(self):
        registry = CircuitBreakerRegistry(
            default_failure_threshold=3,
            default_recovery_timeout=30.0,
        )
        cb = registry.get("test")
        stats = cb.get_stats()
        assert stats["failure_threshold"] == 3
        assert stats["recovery_timeout"] == 30.0

    def test_get_all_states_empty(self):
        registry = CircuitBreakerRegistry()
        assert registry.get_all_states() == {}

    def test_get_all_states_multiple(self):
        registry = CircuitBreakerRegistry()
        registry.get("meshtastic")
        registry.get("reticulum")
        states = registry.get_all_states()
        assert "meshtastic" in states
        assert "reticulum" in states
        assert states["meshtastic"]["state"] == "closed"

    def test_reset_all(self):
        registry = CircuitBreakerRegistry(default_failure_threshold=1)
        cb1 = registry.get("a")
        cb2 = registry.get("b")
        cb1.record_failure()
        cb2.record_failure()
        assert cb1.state == CircuitState.OPEN
        assert cb2.state == CircuitState.OPEN
        registry.reset_all()
        assert cb1.state == CircuitState.CLOSED
        assert cb2.state == CircuitState.CLOSED
