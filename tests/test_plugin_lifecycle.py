"""Tests for PluginLifecycle state machine."""

import pytest

from src.utils.plugin_lifecycle import (
    InvalidTransitionError,
    PluginLifecycle,
    PluginState,
)


class TestPluginStateTransitions:
    """Tests for valid and invalid state transitions."""

    def test_initial_state_is_loaded(self):
        lc = PluginLifecycle()
        assert lc.state == PluginState.LOADED

    def test_loaded_to_activating(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        assert lc.state == PluginState.ACTIVATING

    def test_activating_to_active(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        assert lc.state == PluginState.ACTIVE

    def test_activating_to_error(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ERROR)
        assert lc.state == PluginState.ERROR

    def test_active_to_deactivating(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        lc.transition_to(PluginState.DEACTIVATING)
        assert lc.state == PluginState.DEACTIVATING

    def test_deactivating_to_stopped(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        lc.transition_to(PluginState.DEACTIVATING)
        lc.transition_to(PluginState.STOPPED)
        assert lc.state == PluginState.STOPPED

    def test_stopped_to_activating_restart(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        lc.transition_to(PluginState.DEACTIVATING)
        lc.transition_to(PluginState.STOPPED)
        lc.transition_to(PluginState.ACTIVATING)
        assert lc.state == PluginState.ACTIVATING

    def test_error_to_activating_retry(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ERROR)
        lc.transition_to(PluginState.ACTIVATING)
        assert lc.state == PluginState.ACTIVATING

    @pytest.mark.parametrize("setup_transitions,invalid_target", [
        ([], PluginState.ACTIVE),
        ([], PluginState.DEACTIVATING),
        ([PluginState.ACTIVATING, PluginState.ACTIVE], PluginState.ACTIVATING),
        ([PluginState.ACTIVATING, PluginState.ACTIVE, PluginState.DEACTIVATING, PluginState.STOPPED], PluginState.ACTIVE),
    ])
    def test_invalid_transitions(self, setup_transitions, invalid_target):
        lc = PluginLifecycle()
        for state in setup_transitions:
            lc.transition_to(state)
        with pytest.raises(InvalidTransitionError):
            lc.transition_to(invalid_target)


class TestPluginLifecycleUptime:
    """Tests for uptime tracking."""

    def test_uptime_seconds_when_active(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        uptime = lc.uptime_seconds
        assert uptime is not None
        assert uptime >= 0

    def test_uptime_seconds_when_not_active(self):
        lc = PluginLifecycle()
        assert lc.uptime_seconds is None


class TestPluginLifecycleContextManagers:
    """Tests for the activating() and deactivating() context managers."""

    def test_activating_context_success(self):
        lc = PluginLifecycle()
        with lc.activating():
            assert lc.state == PluginState.ACTIVATING
        assert lc.state == PluginState.ACTIVE

    def test_activating_context_error(self):
        lc = PluginLifecycle()
        with pytest.raises(ValueError):
            with lc.activating():
                raise ValueError("startup failed")
        assert lc.state == PluginState.ERROR
        assert lc.last_error == "startup failed"

    def test_deactivating_context_success(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        with lc.deactivating():
            assert lc.state == PluginState.DEACTIVATING
        assert lc.state == PluginState.STOPPED

    def test_deactivating_context_error(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        with pytest.raises(RuntimeError):
            with lc.deactivating():
                raise RuntimeError("cleanup failed")
        assert lc.state == PluginState.ERROR
        assert lc.last_error == "cleanup failed"


class TestPluginLifecycleRecordError:
    """Tests for record_error method."""

    def test_record_error_transitions_to_error(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.record_error("connection refused")
        assert lc.state == PluginState.ERROR
        assert lc.last_error == "connection refused"

    def test_record_error_from_invalid_state_logs_warning(self):
        lc = PluginLifecycle()
        # LOADED -> ERROR is not allowed in transitions
        # But record_error should handle this gracefully
        lc.record_error("some error")
        assert lc.last_error == "some error"
        # State should still be LOADED (can't transition)
        assert lc.state == PluginState.LOADED

    def test_error_cleared_on_valid_transition(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.record_error("failed")
        assert lc.last_error == "failed"
        lc.transition_to(PluginState.ACTIVATING)  # retry
        assert lc.last_error is None
