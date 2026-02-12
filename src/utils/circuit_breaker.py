"""
MeshForge Maps - Circuit Breaker Pattern

Per-source failure protection for data collectors. When a source
accumulates consecutive failures, the circuit "opens" to stop
requests and prevent timeout cascading. After a recovery timeout,
the circuit enters half-open state to test for recovery.

States:
  CLOSED    -> Normal operation, requests pass through
  OPEN      -> Source is failing, requests are blocked
  HALF_OPEN -> Testing recovery, one request allowed through

Inspired by Netflix Hystrix / MeshForge core gateway/circuit_breaker.py
"""

import logging
import threading
import time
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-source circuit breaker with failure counting and auto-recovery.

    Thread-safe: all state mutations are protected by a lock.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ):
        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float = 0
        self._last_state_change: float = time.time()
        self._total_failures: int = 0
        self._total_successes: int = 0
        self._total_rejected: int = 0
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._check_recovery()
            return self._state

    def can_execute(self) -> bool:
        """Check if a request is allowed through the circuit.

        Returns True if the circuit is CLOSED or HALF_OPEN (testing recovery).
        Returns False if OPEN (source is down, skip it).
        """
        with self._lock:
            self._check_recovery()
            if self._state == CircuitState.OPEN:
                self._total_rejected += 1
                return False
            return True

    def record_success(self) -> None:
        """Record a successful operation. Resets failure count."""
        with self._lock:
            self._total_successes += 1
            self._failure_count = 0
            self._success_count += 1
            if self._state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.CLOSED)
                logger.info(
                    "Circuit breaker '%s' recovered -> CLOSED", self._name
                )
            elif self._state != CircuitState.CLOSED:
                self._transition_to(CircuitState.CLOSED)

    def record_failure(self) -> None:
        """Record a failed operation. Opens circuit if threshold exceeded."""
        with self._lock:
            self._total_failures += 1
            self._failure_count += 1
            self._success_count = 0
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.OPEN)
                logger.warning(
                    "Circuit breaker '%s' recovery failed -> OPEN", self._name
                )
            elif (
                self._state == CircuitState.CLOSED
                and self._failure_count >= self._failure_threshold
            ):
                self._transition_to(CircuitState.OPEN)
                logger.warning(
                    "Circuit breaker '%s' tripped (%d failures) -> OPEN",
                    self._name,
                    self._failure_count,
                )

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state."""
        with self._lock:
            self._failure_count = 0
            self._success_count = 0
            self._transition_to(CircuitState.CLOSED)

    def get_stats(self) -> Dict[str, Any]:
        """Return current circuit breaker statistics."""
        with self._lock:
            self._check_recovery()
            return {
                "name": self._name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "failure_threshold": self._failure_threshold,
                "recovery_timeout": self._recovery_timeout,
                "total_successes": self._total_successes,
                "total_failures": self._total_failures,
                "total_rejected": self._total_rejected,
                "last_failure_time": self._last_failure_time or None,
                "last_state_change": self._last_state_change,
            }

    def _check_recovery(self) -> None:
        """Transition from OPEN to HALF_OPEN if recovery timeout has elapsed.

        Must be called with lock held.
        """
        if self._state != CircuitState.OPEN:
            return
        elapsed = time.time() - self._last_failure_time
        if elapsed >= self._recovery_timeout:
            self._transition_to(CircuitState.HALF_OPEN)
            logger.info(
                "Circuit breaker '%s' recovery timeout elapsed -> HALF_OPEN",
                self._name,
            )

    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state. Must be called with lock held."""
        self._state = new_state
        self._last_state_change = time.time()


class CircuitBreakerRegistry:
    """Registry of named circuit breakers for all data sources.

    Thread-safe: breaker creation and lookup are protected by a lock.
    Includes capacity limit to prevent unbounded growth (upstream pattern
    from meshforge core gateway/circuit_breaker.py).
    """

    # Maximum tracked breakers (prevent unbounded growth)
    MAX_CIRCUITS = 1000

    def __init__(
        self,
        default_failure_threshold: int = 5,
        default_recovery_timeout: float = 60.0,
    ):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._default_failure_threshold = default_failure_threshold
        self._default_recovery_timeout = default_recovery_timeout
        self._lock = threading.Lock()

    def get(
        self,
        name: str,
        failure_threshold: Optional[int] = None,
        recovery_timeout: Optional[float] = None,
    ) -> CircuitBreaker:
        """Get or create a circuit breaker by name."""
        with self._lock:
            if name not in self._breakers:
                # Evict oldest closed breaker if at capacity
                if len(self._breakers) >= self.MAX_CIRCUITS:
                    self._evict_oldest_closed()
                self._breakers[name] = CircuitBreaker(
                    name=name,
                    failure_threshold=failure_threshold
                    if failure_threshold is not None
                    else self._default_failure_threshold,
                    recovery_timeout=recovery_timeout
                    if recovery_timeout is not None
                    else self._default_recovery_timeout,
                )
            return self._breakers[name]

    def get_all_states(self) -> Dict[str, Dict[str, Any]]:
        """Return stats for all registered circuit breakers."""
        with self._lock:
            return {
                name: breaker.get_stats()
                for name, breaker in self._breakers.items()
            }

    def get_open_circuits(self) -> Dict[str, Dict[str, Any]]:
        """Return stats for circuit breakers that are currently OPEN or HALF_OPEN."""
        with self._lock:
            return {
                name: breaker.get_stats()
                for name, breaker in self._breakers.items()
                if breaker.state in (CircuitState.OPEN, CircuitState.HALF_OPEN)
            }

    def reset(self, name: str) -> bool:
        """Reset a specific circuit breaker by name.

        Returns True if the breaker existed and was reset.
        """
        with self._lock:
            breaker = self._breakers.get(name)
            if breaker:
                breaker.reset()
                return True
            return False

    def reset_all(self) -> int:
        """Reset all circuit breakers to CLOSED state.

        Returns the number of breakers that were reset (not already CLOSED).
        """
        count = 0
        with self._lock:
            for breaker in self._breakers.values():
                if breaker.state != CircuitState.CLOSED:
                    breaker.reset()
                    count += 1
        return count

    def _evict_oldest_closed(self) -> None:
        """Evict the oldest CLOSED breaker to make room. Must hold lock."""
        closed = [
            (name, cb)
            for name, cb in self._breakers.items()
            if cb.state == CircuitState.CLOSED
        ]
        if closed:
            oldest = min(closed, key=lambda x: x[1]._last_state_change)
            del self._breakers[oldest[0]]
            logger.debug("Evicted circuit breaker '%s' (capacity limit)", oldest[0])
