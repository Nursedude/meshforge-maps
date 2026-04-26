"""DB inventory — single source of truth for SQLite consumers.

Vendored from /opt/meshforge (commit 40a1b77, 2026-04-26). Domain-wide
DB-bloat closure following the volcanoai 2026-04-26 wedge. This repo
has only one SQLite consumer today (maps_node_history.db) but the
inventory is here so future additions follow the pattern from day 1.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .paths import get_data_dir


@dataclass(frozen=True)
class DBSpec:
    """Contract for a single SQLite database in MeshForge-maps."""
    name: str
    path_factory: Callable[[], Path]
    creator_module: str
    has_auto_prune: bool
    retention_days: Optional[int] = None
    expected_journal_mode: str = "wal"
    expected_synchronous: int = 1
    expected_journal_size_limit: int = 67_108_864
    pragma_overrides: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""


INVENTORY: List[DBSpec] = [
    DBSpec(
        name="maps_node_history",
        path_factory=lambda: get_data_dir() / "maps_node_history.db",
        creator_module="utils.node_history",
        has_auto_prune=True,
        retention_days=3,
        # auto_vacuum=INCREMENTAL must be set BEFORE journal_mode=WAL
        # initializes the DB (order-sensitive). Hand-tuned in
        # _open_connection at src/utils/node_history.py:184–205. The
        # override here is documentation for the audit; the writer
        # owns the actual ordering.
        pragma_overrides={"auto_vacuum": "incremental"},
        notes=(
            "Per-node trajectory observations. 3-day retention with "
            "120s prune cadence. Hand-tuned PRAGMAs (commit 222265e); "
            "deliberately NOT routed through utils.db_helpers because "
            "auto_vacuum=INCREMENTAL must be set before journal_mode=WAL."
        ),
    ),
]


def find_spec(name: str) -> Optional[DBSpec]:
    """Look up a DBSpec by name."""
    for spec in INVENTORY:
        if spec.name == name:
            return spec
    return None
