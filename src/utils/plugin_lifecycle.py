"""
MeshForge Maps - Plugin Lifecycle State Machine

Provides a formal state machine for plugin lifecycle management,
ensuring valid state transitions and enabling diagnostics.

States:
    LOADED     → Plugin class instantiated, no resources allocated
    ACTIVATING → activate() in progress (starting server, registering tools)
    ACTIVE     → Fully running, serving requests
    DEACTIVATING → deactivate() in progress (stopping server, cleanup)
    STOPPED    → Cleanly shut down, resources released
    ERROR      → Activation or runtime error (can retry activate)

Valid transitions:
    LOADED      → ACTIVATING
    ACTIVATING  → ACTIVE | ERROR
    ACTIVE      → DEACTIVATING
    DEACTIVATING → STOPPED | ERROR
    ERROR       → ACTIVATING (retry)
    STOPPED     → ACTIVATING (restart)
"""

import enum
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class PluginState(enum.Enum):
    """Plugin lifecycle states."""
    LOADED = "loaded"
    ACTIVATING = "activating"
    ACTIVE = "active"
    DEACTIVATING = "deactivating"
    STOPPED = "stopped"
    ERROR = "error"


# Valid state transitions: from_state -> set of allowed to_states
_TRANSITIONS: Dict[PluginState, set] = {
    PluginState.LOADED: {PluginState.ACTIVATING},
    PluginState.ACTIVATING: {PluginState.ACTIVE, PluginState.ERROR},
    PluginState.ACTIVE: {PluginState.DEACTIVATING},
    PluginState.DEACTIVATING: {PluginState.STOPPED, PluginState.ERROR},
    PluginState.ERROR: {PluginState.ACTIVATING},
    PluginState.STOPPED: {PluginState.ACTIVATING},
}


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


class PluginLifecycle:
    """State machine for plugin lifecycle management.

    Tracks state transitions, records timing, and provides diagnostics.
    Thread-safe for concurrent state queries (writes are sequential by design
    since activate/deactivate are called from the plugin loader thread).

    Usage:
        lifecycle = PluginLifecycle()
        lifecycle.transition_to(PluginState.ACTIVATING)
        # ... do activation work ...
        lifecycle.transition_to(PluginState.ACTIVE)

        # Or use the context manager helpers:
        with lifecycle.activating():
            start_server()
            register_tools()
        # State is now ACTIVE (or ERROR if an exception was raised)
    """

    def __init__(self) -> None:
        self._state = PluginState.LOADED
        self._history: List[Dict[str, Any]] = [{
            "state": PluginState.LOADED.value,
            "timestamp": time.time(),
        }]
        self._error: Optional[str] = None
        self._listeners: List[Callable[[PluginState, PluginState], None]] = []

    @property
    def state(self) -> PluginState:
        """Current lifecycle state."""
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state == PluginState.ACTIVE

    @property
    def is_stopped(self) -> bool:
        return self._state in (PluginState.STOPPED, PluginState.LOADED)

    @property
    def can_activate(self) -> bool:
        return PluginState.ACTIVATING in _TRANSITIONS.get(self._state, set())

    @property
    def can_deactivate(self) -> bool:
        return PluginState.DEACTIVATING in _TRANSITIONS.get(self._state, set())

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

        old_state = self._state
        self._state = new_state
        self._history.append({
            "state": new_state.value,
            "timestamp": time.time(),
            "from": old_state.value,
        })

        if new_state != PluginState.ERROR:
            self._error = None

        logger.debug("Plugin state: %s → %s", old_state.value, new_state.value)

        for listener in self._listeners:
            try:
                listener(old_state, new_state)
            except Exception as e:
                logger.error("Lifecycle listener error: %s", e)

    def record_error(self, error: str) -> None:
        """Record an error and transition to ERROR state if allowed."""
        self._error = error
        try:
            self.transition_to(PluginState.ERROR)
        except InvalidTransitionError:
            # Can't transition to ERROR from current state — just record it
            logger.warning("Cannot enter ERROR state from %s, error recorded: %s",
                          self._state.value, error)

    def on_transition(self, callback: Callable[[PluginState, PluginState], None]) -> None:
        """Register a callback for state transitions.

        Callback receives (old_state, new_state).
        """
        self._listeners.append(callback)

    def activating(self) -> "_ActivatingContext":
        """Context manager for the activation phase.

        Transitions to ACTIVATING on enter, ACTIVE on successful exit,
        ERROR on exception.
        """
        return _ActivatingContext(self)

    def deactivating(self) -> "_DeactivatingContext":
        """Context manager for the deactivation phase.

        Transitions to DEACTIVATING on enter, STOPPED on successful exit,
        ERROR on exception.
        """
        return _DeactivatingContext(self)

    @property
    def uptime_seconds(self) -> Optional[float]:
        """Seconds since entering ACTIVE state, or None if not active."""
        if self._state != PluginState.ACTIVE:
            return None
        for entry in reversed(self._history):
            if entry["state"] == PluginState.ACTIVE.value:
                return time.time() - entry["timestamp"]
        return None

    @property
    def info(self) -> Dict[str, Any]:
        """Diagnostic info for status endpoints."""
        result: Dict[str, Any] = {
            "state": self._state.value,
            "can_activate": self.can_activate,
            "can_deactivate": self.can_deactivate,
            "transition_count": len(self._history) - 1,
        }
        if self._error:
            result["last_error"] = self._error
        uptime = self.uptime_seconds
        if uptime is not None:
            result["uptime_seconds"] = int(uptime)
        return result


class _ActivatingContext:
    """Context manager for activation phase."""

    def __init__(self, lifecycle: PluginLifecycle):
        self._lifecycle = lifecycle

    def __enter__(self) -> PluginLifecycle:
        self._lifecycle.transition_to(PluginState.ACTIVATING)
        return self._lifecycle

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            self._lifecycle.record_error(str(exc_val))
        else:
            self._lifecycle.transition_to(PluginState.ACTIVE)


class _DeactivatingContext:
    """Context manager for deactivation phase."""

    def __init__(self, lifecycle: PluginLifecycle):
        self._lifecycle = lifecycle

    def __enter__(self) -> PluginLifecycle:
        self._lifecycle.transition_to(PluginState.DEACTIVATING)
        return self._lifecycle

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            self._lifecycle.record_error(str(exc_val))
        else:
            self._lifecycle.transition_to(PluginState.STOPPED)
