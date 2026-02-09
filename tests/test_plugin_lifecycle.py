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

    def test_invalid_loaded_to_active(self):
        lc = PluginLifecycle()
        with pytest.raises(InvalidTransitionError):
            lc.transition_to(PluginState.ACTIVE)

    def test_invalid_loaded_to_deactivating(self):
        lc = PluginLifecycle()
        with pytest.raises(InvalidTransitionError):
            lc.transition_to(PluginState.DEACTIVATING)

    def test_invalid_active_to_activating(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        with pytest.raises(InvalidTransitionError):
            lc.transition_to(PluginState.ACTIVATING)

    def test_invalid_stopped_to_active(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        lc.transition_to(PluginState.DEACTIVATING)
        lc.transition_to(PluginState.STOPPED)
        with pytest.raises(InvalidTransitionError):
            lc.transition_to(PluginState.ACTIVE)


class TestPluginLifecycleProperties:
    """Tests for lifecycle property accessors."""

    def test_is_active_when_active(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        assert lc.is_active is True

    def test_is_active_when_not_active(self):
        lc = PluginLifecycle()
        assert lc.is_active is False

    def test_is_stopped_when_loaded(self):
        lc = PluginLifecycle()
        assert lc.is_stopped is True

    def test_is_stopped_when_stopped(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        lc.transition_to(PluginState.DEACTIVATING)
        lc.transition_to(PluginState.STOPPED)
        assert lc.is_stopped is True

    def test_is_stopped_when_active(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        assert lc.is_stopped is False

    def test_can_activate_from_loaded(self):
        lc = PluginLifecycle()
        assert lc.can_activate is True

    def test_can_activate_from_error(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ERROR)
        assert lc.can_activate is True

    def test_cannot_activate_from_active(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        assert lc.can_activate is False

    def test_can_deactivate_from_active(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        assert lc.can_deactivate is True

    def test_cannot_deactivate_from_loaded(self):
        lc = PluginLifecycle()
        assert lc.can_deactivate is False

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


class TestPluginLifecycleListeners:
    """Tests for state transition listeners."""

    def test_listener_called_on_transition(self):
        lc = PluginLifecycle()
        transitions = []
        lc.on_transition(lambda old, new: transitions.append((old.value, new.value)))

        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)

        assert len(transitions) == 2
        assert transitions[0] == ("loaded", "activating")
        assert transitions[1] == ("activating", "active")

    def test_listener_error_does_not_prevent_transition(self):
        lc = PluginLifecycle()

        def bad_listener(old, new):
            raise ValueError("listener error")

        lc.on_transition(bad_listener)
        lc.transition_to(PluginState.ACTIVATING)
        assert lc.state == PluginState.ACTIVATING  # Should still transition


class TestPluginLifecycleInfo:
    """Tests for the info diagnostic property."""

    def test_info_initial(self):
        lc = PluginLifecycle()
        info = lc.info
        assert info["state"] == "loaded"
        assert info["can_activate"] is True
        assert info["can_deactivate"] is False
        assert info["transition_count"] == 0

    def test_info_when_active(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.transition_to(PluginState.ACTIVE)
        info = lc.info
        assert info["state"] == "active"
        assert "uptime_seconds" in info
        assert info["transition_count"] == 2

    def test_info_with_error(self):
        lc = PluginLifecycle()
        lc.transition_to(PluginState.ACTIVATING)
        lc.record_error("test error")
        info = lc.info
        assert info["state"] == "error"
        assert info["last_error"] == "test error"
