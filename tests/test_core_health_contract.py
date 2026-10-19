"""Contract test: maps reads the health DB MeshForge core ACTUALLY writes.

Domain audit (2026-10-18): the reader queried ``node_health`` and
``latency_stats`` (tables MeshForge never creates) and ``service_health``
columns that do not exist, caught the error, and returned ``[]`` — so
``/api/core-health`` was always empty and looked healthy. Its tests passed
because they built the same invented schema.

So the producer here is MeshForge's REAL ``SharedHealthState``, run in a
subprocess with MeshForge's ``src`` on its path (both repos ship a ``utils``
package; one interpreter cannot import both). Where MeshForge comes from:
``$MESHFORGE_SRC``, else ``/opt/meshforge/src``. In CI its absence FAILS —
a contract test that skips where it matters pins nothing.
"""
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from src.utils.shared_health_state import SharedHealthStateReader


def _meshforge_src():
    if sys.version_info < (3, 11):
        # MeshForge declares requires-python >=3.11 and the checkout is its
        # default branch: running its code on 3.9 would go red on a MF
        # change no maps commit made. The 3.11 leg is the contract (and
        # FAILS there when the source is missing).
        pytest.skip("MeshForge (the producer) requires Python >= 3.11")
    def ok(p):
        return (Path(p) / "utils" / "shared_health_state.py").exists()
    explicit = os.environ.get("MESHFORGE_SRC")
    if explicit:  # set but wrong is a broken setup, never a quiet fallback
        if ok(explicit):
            return explicit
        pytest.fail(f"MESHFORGE_SRC={explicit!r} has no "
                    "utils/shared_health_state.py")
    if ok("/opt/meshforge/src"):
        return "/opt/meshforge/src"
    if os.environ.get("CI"):
        pytest.fail("MESHFORGE_SRC not available in CI — the contract test "
                    "must run against MeshForge's real producer")
    pytest.skip("MeshForge source not present on this machine")


def _produce(db: Path, body: str) -> None:
    """Run ``body`` with ``s = SharedHealthState(db_path=db)`` in MeshForge's
    own interpreter context."""
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {_meshforge_src()!r})
        from pathlib import Path
        from utils.shared_health_state import SharedHealthState
        s = SharedHealthState(db_path=Path({str(db)!r}))
    """) + textwrap.dedent(body)
    path = db.parent / "producer.py"
    path.write_text(script)
    subprocess.run([sys.executable, "-I", str(path)], check=True, timeout=60,
                   cwd=str(db.parent))


def test_reports_the_services_meshforge_wrote(tmp_path):
    db = tmp_path / "health_state.db"
    _produce(db, """
        s.update_service("meshtasticd", "healthy", latency_ms=12.5)
        s.update_service("rnsd", "unhealthy", reason="timeout")
    """)
    summary = SharedHealthStateReader(db_path=db).get_summary()
    assert summary["status"] == "ok", summary
    by = {svc["service"]: svc for svc in summary["services"]}
    assert by["meshtasticd"]["state"] == "healthy"
    assert by["rnsd"]["state"] == "unhealthy"
    assert by["rnsd"]["reason"] == "timeout"
    assert by["meshtasticd"]["stale"] is False


def test_latency_percentiles_come_from_latency_samples(tmp_path):
    db = tmp_path / "health_state.db"
    _produce(db, """
        for ms in (10, 20, 30, 40, 50):
            s.update_service("meshtasticd", "healthy", latency_ms=ms)
    """)
    lat = SharedHealthStateReader(db_path=db).get_summary()["latency"]
    assert lat["meshtasticd"]["count"] == 5
    assert lat["meshtasticd"]["p50"] == 30
    assert lat["meshtasticd"]["max"] == 50


def test_a_stale_service_is_not_reported_as_its_last_state(tmp_path):
    """MeshForge calls a row stale after 120 s without an update; a stale
    'healthy' is "can't tell", never healthy."""
    db = tmp_path / "health_state.db"
    _produce(db, 's.update_service("meshtasticd", "healthy")')
    conn = sqlite3.connect(db)
    conn.execute("UPDATE service_health SET updated_at = ?",
                 (time.time() - 3600,))
    conn.commit()
    conn.close()
    svc = SharedHealthStateReader(db_path=db).get_summary()["services"][0]
    assert svc["stale"] is True
    assert svc["state"] == "stale"
    assert svc["last_state"] == "healthy"


def test_core_ran_but_recorded_nothing_says_empty_not_ok(tmp_path):
    db = tmp_path / "health_state.db"
    _produce(db, "pass")
    summary = SharedHealthStateReader(db_path=db).get_summary()
    assert summary["status"] == "empty"
    assert summary["services"] == []


def test_no_db_says_no_db(tmp_path):
    summary = SharedHealthStateReader(db_path=tmp_path / "absent.db").get_summary()
    assert summary["available"] is False
    assert summary["status"] == "no_db"


def test_unreadable_schema_says_cant_tell_never_empty(tmp_path):
    """The defect class itself: a read error must not become an empty list
    that reads as 'no problems'."""
    db = tmp_path / "health_state.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE service_health (bogus TEXT)")
    conn.commit()
    conn.close()
    summary = SharedHealthStateReader(db_path=db).get_summary()
    assert summary["status"] == "unreadable"
    assert summary["error"]
    assert summary["available"] is False


# --- non-author review (Fable 5.1, 2026-10-18) ---------------------------

def _age_rows(db, seconds):
    conn = sqlite3.connect(db)
    conn.execute("UPDATE service_health SET updated_at = ?",
                 (time.time() - seconds,))
    conn.commit()
    conn.close()


def test_every_row_stale_says_stale_not_ok(tmp_path):
    """A producer that ran once and died: the summary must not read ok."""
    db = tmp_path / "health_state.db"
    _produce(db, """
        s.update_service("meshtasticd", "healthy")
        s.update_service("rnsd", "healthy")
    """)
    _age_rows(db, 86400)
    summary = SharedHealthStateReader(db_path=db).get_summary()
    assert summary["status"] == "stale"
    assert summary["stale_count"] == 2


def test_a_row_from_the_future_is_cant_tell_not_fresh(tmp_path):
    """RTC-less Pis step their clocks: a row stamped ahead of now is not
    evidence of a fresh, healthy service (MF honest_failure_modes #6)."""
    db = tmp_path / "health_state.db"
    _produce(db, 's.update_service("meshtasticd", "healthy")')
    _age_rows(db, -3600)
    svc = SharedHealthStateReader(db_path=db).get_summary()["services"][0]
    assert svc["stale"] is True and svc["state"] == "stale"
    assert "clock" in svc["stale_reason"]


def test_a_db_that_exists_but_cannot_open_is_unreadable_not_absent(tmp_path):
    db = tmp_path / "health_state.db"
    _produce(db, 's.update_service("meshtasticd", "healthy")')
    db.chmod(0)
    try:
        if os.access(db, os.R_OK):
            pytest.skip("running as root: chmod cannot deny the read")
        summary = SharedHealthStateReader(db_path=db).get_summary()
    finally:
        db.chmod(0o644)
    assert summary["status"] == "unreadable"
    assert summary["error"]


def test_closed_reader_says_closed(tmp_path):
    db = tmp_path / "health_state.db"
    _produce(db, 's.update_service("meshtasticd", "healthy")')
    r = SharedHealthStateReader(db_path=db)
    r.close()
    assert r.get_summary()["status"] == "closed"


def test_db_that_appears_after_the_reader_is_read_not_called_unreadable(tmp_path):
    """Review round 2: "unreadable/cannot open" must follow a REAL failed
    open, never stand in for an open that was never tried."""
    db = tmp_path / "health_state.db"
    r = SharedHealthStateReader(db_path=db)
    assert r.get_summary()["status"] == "no_db"
    _produce(db, 's.update_service("meshtasticd", "healthy")')
    assert r.get_summary()["status"] == "ok"


def test_close_means_close(tmp_path):
    db = tmp_path / "health_state.db"
    _produce(db, 's.update_service("meshtasticd", "healthy")')
    r = SharedHealthStateReader(db_path=db)
    r.close()
    assert r.refresh() is False
    assert r.get_summary()["status"] == "closed"
