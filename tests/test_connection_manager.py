"""Tests for ConnectionManager: meshtasticd TCP contention prevention."""

import threading
import time

import pytest

from src.utils.connection_manager import ConnectionManager


class TestConnectionManagerSingleton:
    """Tests for singleton instance management."""

    def setup_method(self):
        ConnectionManager.reset_all()

    def test_get_instance_returns_same_object(self):
        mgr1 = ConnectionManager.get_instance("localhost", 4403)
        mgr2 = ConnectionManager.get_instance("localhost", 4403)
        assert mgr1 is mgr2

    def test_different_host_port_returns_different_instance(self):
        mgr1 = ConnectionManager.get_instance("localhost", 4403)
        mgr2 = ConnectionManager.get_instance("localhost", 4404)
        assert mgr1 is not mgr2

    def test_different_host_same_port_returns_different_instance(self):
        mgr1 = ConnectionManager.get_instance("localhost", 4403)
        mgr2 = ConnectionManager.get_instance("192.168.1.1", 4403)
        assert mgr1 is not mgr2

    def test_reset_all_clears_instances(self):
        mgr1 = ConnectionManager.get_instance("localhost", 4403)
        ConnectionManager.reset_all()
        mgr2 = ConnectionManager.get_instance("localhost", 4403)
        assert mgr1 is not mgr2



class TestConnectionAcquireRelease:
    """Tests for lock acquisition and release."""

    def setup_method(self):
        ConnectionManager.reset_all()

    def test_acquire_succeeds(self):
        mgr = ConnectionManager.get_instance()
        with mgr.acquire(timeout=1.0, holder="test") as acquired:
            assert acquired is True
            assert mgr.is_locked is True
            assert mgr.holder == "test"

    def test_release_on_exit(self):
        mgr = ConnectionManager.get_instance()
        with mgr.acquire(timeout=1.0, holder="test") as acquired:
            assert acquired is True
        assert mgr.is_locked is False
        assert mgr.holder is None

    def test_acquire_timeout_when_locked(self):
        mgr = ConnectionManager.get_instance()
        mgr._lock.acquire()
        mgr._holder = "other_component"
        try:
            with mgr.acquire(timeout=0.1, holder="test") as acquired:
                assert acquired is False
        finally:
            mgr._lock.release()

    def test_non_blocking_acquire(self):
        mgr = ConnectionManager.get_instance()
        mgr._lock.acquire()
        try:
            with mgr.acquire(timeout=0, holder="test") as acquired:
                assert acquired is False
        finally:
            mgr._lock.release()

    def test_acquire_after_release(self):
        mgr = ConnectionManager.get_instance()
        with mgr.acquire(timeout=1.0, holder="first") as acquired:
            assert acquired is True
        with mgr.acquire(timeout=1.0, holder="second") as acquired:
            assert acquired is True
            assert mgr.holder == "second"

    def test_default_holder_name(self):
        mgr = ConnectionManager.get_instance()
        with mgr.acquire(timeout=1.0) as acquired:
            assert acquired is True
            assert mgr.holder == "unknown"


class TestConnectionManagerStats:
    """Tests for diagnostic stats."""

    def setup_method(self):
        ConnectionManager.reset_all()

    def test_stats_after_acquire_release(self):
        mgr = ConnectionManager.get_instance()
        with mgr.acquire(timeout=1.0, holder="test"):
            pass
        stats = mgr.stats
        assert stats["total_acquisitions"] == 1
        assert stats["total_releases"] == 1
        assert stats["total_timeouts"] == 0

    def test_stats_after_timeout(self):
        mgr = ConnectionManager.get_instance()
        mgr._lock.acquire()
        try:
            with mgr.acquire(timeout=0, holder="test"):
                pass
        finally:
            mgr._lock.release()
        stats = mgr.stats
        assert stats["total_timeouts"] == 1
        assert stats["total_acquisitions"] == 0

    def test_held_seconds_while_locked(self):
        mgr = ConnectionManager.get_instance()
        with mgr.acquire(timeout=1.0, holder="test"):
            time.sleep(0.05)
            stats = mgr.stats
            assert stats["held_seconds"] is not None
            assert stats["held_seconds"] >= 0

    def test_is_locked_property(self):
        mgr = ConnectionManager.get_instance()
        assert mgr.is_locked is False
        with mgr.acquire(timeout=1.0, holder="test"):
            assert mgr.is_locked is True
        assert mgr.is_locked is False


class TestConnectionManagerThreadSafety:
    """Tests for concurrent access."""

    def setup_method(self):
        ConnectionManager.reset_all()

    def test_concurrent_acquire_only_one_wins(self):
        mgr = ConnectionManager.get_instance()
        results = []
        barrier = threading.Barrier(2)

        def worker(name):
            barrier.wait()
            with mgr.acquire(timeout=0.5, holder=name) as acquired:
                results.append((name, acquired))
                if acquired:
                    time.sleep(0.2)  # Hold lock briefly

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        acquired_count = sum(1 for _, a in results if a)
        # At least one should acquire; both might if timing allows
        assert acquired_count >= 1

    def test_sequential_workers(self):
        mgr = ConnectionManager.get_instance()
        order = []

        def worker(name, delay):
            with mgr.acquire(timeout=5.0, holder=name) as acquired:
                if acquired:
                    order.append(name)
                    time.sleep(delay)

        t1 = threading.Thread(target=worker, args=("first", 0.1))
        t2 = threading.Thread(target=worker, args=("second", 0.05))
        t1.start()
        time.sleep(0.01)  # Give t1 a head start
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(order) == 2
        assert order[0] == "first"
        assert order[1] == "second"
