"""
MeshForge Maps - Plugin Lifecycle State Machine

Simple state machine for plugin activate/deactivate transitions.

States:
    LOADED      → Plugin class instantiated, no resources allocated
    ACTIVATING  → activate() in progress
    ACTIVE      → Fully running, serving requests
    DEACTIVATING → deactivate() in progress
    STOPPED     → Cleanly shut down, resources released
    ERROR       → Activation or runtime error (can retry activate)

Valid transitions:
    LOADED       → ACTIVATING
    ACTIVATING   → ACTIVE | ERROR
    ACTIVE       → DEACTIVATING
    DEACTIVATING → STOPPED | ERROR
    ERROR        → ACTIVATING (retry)
    STOPPED      → ACTIVATING (restart)
"""

import enum
import logging
import time
from contextlib import contextmanager
from typing import Dict, Iterator, Optional, Set

logger = logging.getLogger(__name__)


class PluginState(enum.Enum):
    """Plugin lifecycle states."""
    LOADED = "loaded"
    ACTIVATING = "activating"
    ACTIVE = "active"
    DEACTIVATING = "deactivating"
    STOPPED = "stopped"
    ERROR = "error"


_TRANSITIONS: Dict[PluginState, Set[PluginState]] = {
    PluginState.LOADED: {PluginState.ACTIVATING},
    PluginState.ACTIVATING: {PluginState.ACTIVE, PluginState.ERROR},
    PluginState.ACTIVE: {PluginState.DEACTIVATING},
    PluginState.DEACTIVATING: {PluginState.STOPPED, PluginState.ERROR},
    PluginState.ERROR: {PluginState.ACTIVATING},
    PluginState.STOPPED: {PluginState.ACTIVATING},
}


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""


class PluginLifecycle:
    """State machine for plugin lifecycle management.

    Usage::

        lifecycle = PluginLifecycle()

        # Manual transitions:
        lifecycle.transition_to(PluginState.ACTIVATING)
        lifecycle.transition_to(PluginState.ACTIVE)

        # Or use context managers:
        with lifecycle.activating():
            start_server()
        # State is now ACTIVE (or ERROR if an exception was raised)
    """

    def __init__(self) -> None:
        self._state = PluginState.LOADED
        self._error: Optional[str] = None
        self._activated_at: Optional[float] = None

    @property
    def state(self) -> PluginState:
        """Current lifecycle state."""
        return self._state

    @property
    def last_error(self) -> Optional[str]:
        return self._error

    def transition_to(self, new_state: PluginState) -> None:
        """Transition to a new state, validating the transition is allowed.

        Raises InvalidTransitionError if the transition is not valid.
        """
        allowed = _TRANSITIONS.get(self._state, set())
        if new_state not in allowed:
            raise InvalidTransitionError(
                f"Cannot transition from {self._state.value} to {new_state.value}. "
                f"Allowed: {[s.value for s in allowed]}"
            )
        old = self._state
        self._state = new_state

        if new_state == PluginState.ACTIVE:
            self._activated_at = time.time()
        if new_state != PluginState.ERROR:
            self._error = None

        logger.debug("Plugin state: %s → %s", old.value, new_state.value)

    def record_error(self, error: str) -> None:
        """Record an error and transition to ERROR state if allowed."""
        self._error = error
        try:
            self.transition_to(PluginState.ERROR)
        except InvalidTransitionError:
            logger.warning(
                "Cannot enter ERROR state from %s, error recorded: %s",
                self._state.value, error,
            )

    @property
    def uptime_seconds(self) -> Optional[float]:
        """Seconds since entering ACTIVE state, or None if not active."""
        if self._state != PluginState.ACTIVE or self._activated_at is None:
            return None
        return time.time() - self._activated_at

    @contextmanager
    def activating(self) -> Iterator["PluginLifecycle"]:
        """Context manager for activation phase.

        Transitions to ACTIVATING on enter, ACTIVE on successful exit,
        ERROR on exception.
        """
        self.transition_to(PluginState.ACTIVATING)
        try:
            yield self
        except Exception as exc:
            self.record_error(str(exc))
            raise
        else:
            self.transition_to(PluginState.ACTIVE)

    @contextmanager
    def deactivating(self) -> Iterator["PluginLifecycle"]:
        """Context manager for deactivation phase.

        Transitions to DEACTIVATING on enter, STOPPED on successful exit,
        ERROR on exception.
        """
        self.transition_to(PluginState.DEACTIVATING)
        try:
            yield self
        except Exception as exc:
            self.record_error(str(exc))
            raise
        else:
            self.transition_to(PluginState.STOPPED)
