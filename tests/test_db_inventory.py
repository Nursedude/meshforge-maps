"""Tests for src.utils.db_inventory — domain-wide closure.

Vendored from /opt/meshforge (commit 40a1b77, 2026-04-26)."""

import re
from pathlib import Path
from typing import Set

import pytest

from src.utils.db_inventory import INVENTORY, DBSpec, find_spec


REPO_SRC = Path(__file__).resolve().parent.parent / "src"


class TestDBSpec:
    def test_all_specs_have_path_factory(self):
        for spec in INVENTORY:
            path = spec.path_factory()
            assert isinstance(path, Path), f"{spec.name}: must return Path"

    def test_all_specs_have_unique_names(self):
        names = [s.name for s in INVENTORY]
        assert len(names) == len(set(names))

    def test_all_specs_have_unique_paths(self):
        paths = [str(s.path_factory()) for s in INVENTORY]
        assert len(paths) == len(set(paths))

    def test_pragma_defaults_match_helper(self):
        for spec in INVENTORY:
            assert spec.expected_journal_mode == "wal"
            assert spec.expected_synchronous == 1
            assert spec.expected_journal_size_limit == 67_108_864

    def test_find_spec_returns_correct_db(self):
        spec = find_spec("maps_node_history")
        assert spec is not None
        assert spec.creator_module == "utils.node_history"

    def test_find_spec_returns_none_for_unknown(self):
        assert find_spec("does_not_exist_xyz") is None


class TestEveryRuntimeDBIsInInventory:
    """Forgot-to-register safety net.

    Scans src/ for `.db` path literals and asserts each appears in
    INVENTORY.path_factory(). meshforge-maps has only one DB today;
    this test guards against silent additions."""

    DB_LITERAL = re.compile(r'["\']([a-z_][a-z0-9_]*)\.db["\']')

    def _runtime_db_basenames(self) -> Set[str]:
        names: Set[str] = set()
        for py_file in REPO_SRC.rglob("*.py"):
            if py_file.name == "db_inventory.py":
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for match in self.DB_LITERAL.finditer(content):
                name = match.group(1)
                if name in {"test", "tmp", "x", "foo", "bar", "stub"}:
                    continue
                names.add(name)
        return names

    def _inventory_basenames(self) -> Set[str]:
        return {Path(str(s.path_factory())).stem for s in INVENTORY}

    # DBs we read from but don't own (external schemas owned by sibling
    # repos like /opt/meshforge core). Don't inventory these — we don't
    # control their PRAGMAs or retention.
    EXTERNAL_READERS = {"health_state"}

    def test_every_runtime_db_in_inventory(self):
        runtime = self._runtime_db_basenames()
        inventory = self._inventory_basenames()
        missing = runtime - inventory - self.EXTERNAL_READERS
        assert not missing, (
            f"DBs found in src/ but missing from INVENTORY: {missing}. "
            "Add a DBSpec to src/utils/db_inventory.py "
            "(or extend EXTERNAL_READERS if it's a cross-repo reader)."
        )
