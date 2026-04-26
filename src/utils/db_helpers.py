"""SQLite connection helpers — single source of truth for tuned pragmas.

Vendored from /opt/meshforge (commit 2743ded, 2026-04-26). The core
repo's fleet-host-2026-04-26 wedge (1.95 GB rollback-journal-mode DB
stalled the :5000 service for 16+ minutes in jbd2_log_wait_commit)
prompted a domain-wide audit. maps_node_history.db here was already
hardened (commit 222265e, 2026-04-20) with WAL +
journal_size_limit + auto_vacuum=INCREMENTAL — that hand-tuned setup
is preserved because PRAGMA order matters (auto_vacuum must be set
BEFORE journal_mode=WAL initializes the DB).

This helper exists for FUTURE SQLite consumers added to this repo —
they should go through connect_tuned to get WAL + synchronous=NORMAL
+ journal_size_limit=64MB by default. If a new use case needs auto_vacuum
or a different busy_timeout, copy the maps_node_history pattern instead.

Usage:
    from .db_helpers import connect_tuned

    conn = connect_tuned(self.db_path)
    try:
        conn.execute("INSERT INTO ...")
        conn.commit()
    finally:
        conn.close()
"""

import sqlite3
from pathlib import Path
from typing import Union

# 64 MB cap on WAL/journal growth. Matches maps_node_history.db
# (commit 222265e). Lower risks frequent checkpoints; higher risks
# the multi-GB SD-card wedge we just fixed in core.
DEFAULT_JOURNAL_SIZE_LIMIT = 67_108_864

# busy_timeout — how long a writer waits for a lock before SQLITE_BUSY.
# 30 s is generous for Pi-class storage where checkpoints can briefly
# block writers. Reader-heavy DBs (like maps_node_history.db) use 5 s
# instead so a wedged writer doesn't stall the request path.
DEFAULT_BUSY_TIMEOUT_SECONDS = 30.0


# Sentinel for "use sqlite3's default isolation_level (deferred autocommit)".
_DEFAULT_ISOLATION = object()


def connect_tuned(
    path: Union[str, Path],
    *,
    busy_timeout_seconds: float = DEFAULT_BUSY_TIMEOUT_SECONDS,
    journal_size_limit: int = DEFAULT_JOURNAL_SIZE_LIMIT,
    check_same_thread: bool = True,
    isolation_level=_DEFAULT_ISOLATION,
    uri: bool = False,
) -> sqlite3.Connection:
    """Open a SQLite connection with the MeshForge-standard pragmas.

    - journal_mode=WAL: per-commit fsyncs no longer rewrite the entire
      DB file. The change is persistent on the DB header — first open
      after a fresh file (or rollback-journal DB) performs the conversion.
    - synchronous=NORMAL: with WAL this is durable across power loss
      across most-recent commits; sufficient for telemetry. Per-connection.
    - journal_size_limit: caps WAL file growth so a long-running writer
      can't balloon it to multi-GB.
    - busy_timeout: configured via sqlite3.connect's `timeout` parameter,
      which sets PRAGMA busy_timeout for us.

    Args:
        path: Database file path (str or Path).
        busy_timeout_seconds: How long a writer waits for a lock.
        journal_size_limit: Cap on WAL file size in bytes.
        check_same_thread: Pass-through to sqlite3.connect.
        isolation_level: Pass-through. Default keeps sqlite3's default.
        uri: Pass-through. Set True for "file:/.../db?mode=ro" URIs.

    Returns:
        A tuned sqlite3.Connection. Caller owns lifecycle (close it).
    """
    kwargs = dict(
        timeout=busy_timeout_seconds,
        check_same_thread=check_same_thread,
        uri=uri,
    )
    if isolation_level is not _DEFAULT_ISOLATION:
        kwargs["isolation_level"] = isolation_level
    conn = sqlite3.connect(str(path), **kwargs)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA journal_size_limit={int(journal_size_limit)}")
    return conn
