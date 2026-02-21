"""
MeshForge Maps - Plugin Lifecycle Tracker

Simple active/inactive tracker for plugin state. Replaces the previous
full state machine (LOADED/ACTIVATING/ACTIVE/DEACTIVATING/STOPPED/ERROR)
with a minimal implementation since there is only one plugin.
"""

import enum
import logging
import time
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


class PluginState(enum.Enum):
    """Plugin lifecycle states (simplified)."""
    LOADED = "loaded"
    ACTIVATING = "activating"
    ACTIVE = "active"
    DEACTIVATING = "deactivating"
    STOPPED = "stopped"
    ERROR = "error"


class PluginLifecycle:
    """Simplified plugin lifecycle tracker.

    Tracks whether the plugin is active and records errors.
    Accepts any state transition (no validation needed for a single plugin).
    """

    def __init__(self) -> None:
        self._state = PluginState.LOADED
        self._error: Optional[str] = None
        self._activated_at: Optional[float] = None

    @property
    def state(self) -> PluginState:
        return self._state

    @property
    def last_error(self) -> Optional[str]:
        return self._error

    def transition_to(self, new_state: PluginState) -> None:
        """Transition to a new state."""
        old = self._state
        self._state = new_state
        if new_state == PluginState.ACTIVE:
            self._activated_at = time.time()
        if new_state != PluginState.ERROR:
            self._error = None
        logger.debug("Plugin state: %s -> %s", old.value, new_state.value)

    def record_error(self, error: str) -> None:
        """Record an error and transition to ERROR state."""
        self._error = error
        self._state = PluginState.ERROR

    @property
    def uptime_seconds(self) -> Optional[float]:
        if self._state != PluginState.ACTIVE or self._activated_at is None:
            return None
        return time.time() - self._activated_at

    @contextmanager
    def activating(self) -> Iterator["PluginLifecycle"]:
        """Context manager for activation phase."""
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
        """Context manager for deactivation phase."""
        self.transition_to(PluginState.DEACTIVATING)
        try:
            yield self
        except Exception as exc:
            self.record_error(str(exc))
            raise
        else:
            self.transition_to(PluginState.STOPPED)
