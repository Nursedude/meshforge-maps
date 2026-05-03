"""Tests for scripts/db_audit.py — synthetic-DB exercise of audit logic.

Vendored from /opt/meshforge (commit 40a1b77, 2026-04-26)."""

import importlib.util
import sqlite3
from pathlib import Path

from src.utils.db_inventory import DBSpec
from src.utils.db_helpers import connect_tuned

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "db_audit.py"
)
_spec = importlib.util.spec_from_file_location("db_audit", _SCRIPT_PATH)
_db_audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_db_audit)
audit_db = _db_audit.audit_db


def _make_spec(name: str, path: Path) -> DBSpec:
    return DBSpec(
        name=name,
        path_factory=lambda: path,
        creator_module="test",
        has_auto_prune=True,
        retention_days=7,
    )


class TestAuditNotCreated:
    def test_missing_db_reports_not_created(self, tmp_path):
        spec = _make_spec("missing", tmp_path / "missing.db")
        r = audit_db(spec, max_db_mb=500, max_wal_mb=100, mode_mask=0o002)
        assert r.exists is False
        assert r.verdict == "NOT_CREATED"


class TestAuditOK:
    def test_freshly_tuned_db_is_ok(self, tmp_path):
        path = tmp_path / "fresh.db"
        conn = connect_tuned(path)
        conn.execute("CREATE TABLE foo (id INTEGER)")
        conn.commit()
        conn.close()
        spec = _make_spec("fresh", path)
        r = audit_db(spec, max_db_mb=500, max_wal_mb=100, mode_mask=0o002)
        assert r.exists is True
        assert r.verdict == "OK", f"unexpected issues: {r.issues}"
        assert r.journal_mode == "wal"
        assert r.schema_tables == 1


class TestAuditPragmaDrift:
    def test_rollback_journal_db_is_FAIL(self, tmp_path):
        path = tmp_path / "rollback.db"
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("CREATE TABLE bar (id INTEGER)")
        conn.commit()
        conn.close()
        spec = _make_spec("rollback", path)
        r = audit_db(spec, max_db_mb=500, max_wal_mb=100, mode_mask=0o002)
        assert r.verdict == "FAIL"
        assert any("journal_mode" in i for i in r.issues)


class TestAuditPermissionGap:
    def test_world_writable_is_FAIL(self, tmp_path):
        path = tmp_path / "perm.db"
        conn = connect_tuned(path)
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()
        conn.close()
        path.chmod(0o666)
        spec = _make_spec("perm", path)
        r = audit_db(spec, max_db_mb=500, max_wal_mb=100, mode_mask=0o002)
        assert r.verdict == "FAIL"
        assert any("mode" in i for i in r.issues)


class TestAuditEmptySchema:
    def test_empty_db_FAIL(self, tmp_path):
        path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(path))
        conn.close()
        spec = _make_spec("empty", path)
        r = audit_db(spec, max_db_mb=500, max_wal_mb=100, mode_mask=0o002)
        assert r.verdict == "FAIL"
        assert any("zero tables" in i for i in r.issues)
