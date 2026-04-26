"""Tests for src.utils.db_helpers.connect_tuned — vendored helper.

Locks in the contract for any future SQLite consumer added to this
repo: WAL + synchronous=NORMAL + journal_size_limit=64MB +
busy_timeout=30s. The existing maps_node_history.db opens its own
connection (with auto_vacuum=INCREMENTAL ordering that requires
manual handling) and is intentionally NOT routed through this helper."""

from pathlib import Path

import pytest

from src.utils.db_helpers import (
    DEFAULT_BUSY_TIMEOUT_SECONDS,
    DEFAULT_JOURNAL_SIZE_LIMIT,
    connect_tuned,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_tuned.db"


class TestConnectTuned:
    def test_journal_mode_is_wal(self, db_path: Path):
        conn = connect_tuned(db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            conn.close()

    def test_synchronous_is_normal(self, db_path: Path):
        conn = connect_tuned(db_path)
        try:
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
            assert sync == 1
        finally:
            conn.close()

    def test_journal_size_limit_default_64mb(self, db_path: Path):
        conn = connect_tuned(db_path)
        try:
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert limit == DEFAULT_JOURNAL_SIZE_LIMIT == 67_108_864
        finally:
            conn.close()

    def test_busy_timeout_default_30s(self, db_path: Path):
        conn = connect_tuned(db_path)
        try:
            ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert ms == int(DEFAULT_BUSY_TIMEOUT_SECONDS * 1000) == 30_000
        finally:
            conn.close()

    def test_custom_busy_timeout(self, db_path: Path):
        conn = connect_tuned(db_path, busy_timeout_seconds=5.0)
        try:
            ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert ms == 5_000
        finally:
            conn.close()

    def test_uri_mode_passes_through(self, db_path: Path):
        # Create a writable DB first, then re-open in read-only URI mode.
        c1 = connect_tuned(db_path)
        c1.execute("CREATE TABLE t (id INTEGER)")
        c1.commit()
        c1.close()
        ro_uri = f"file:{db_path}?mode=ro"
        c2 = connect_tuned(ro_uri, uri=True)
        try:
            # Read-only DB should still report WAL (set on header by writer).
            mode = c2.execute("PRAGMA journal_mode").fetchone()[0]
            # Read-only connections can't change journal_mode; report whatever
            # the DB header says — should be 'wal' since c1 set it.
            assert mode.lower() in ("wal", "delete")
        finally:
            c2.close()
