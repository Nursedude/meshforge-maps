# Future Implementation Sessions

Two planned enhancement sessions for meshforge-maps, building on existing
infrastructure without introducing new external dependencies (stdlib-only for core).

---

## Session 2: Operational Visibility

The project has PerfMonitor, HealthScoring, AlertEngine, Analytics, and a 7-tab TUI.
However, system-level observability has gaps: no latency percentiles, no per-endpoint
request metrics, no SLA tracking, no slow-query logging, and no memory usage snapshots.
This session closes those gaps.

### Priority 1 -- Latency Percentile Tracking

**File:** `src/utils/perf_monitor.py`

`PerfMonitor` stores only `min_ms`, `max_ms`, and cumulative `total_ms / count` per
source (line 51-56). No sample history exists for percentile calculations.

**Changes:**
- Add a `collections.deque(maxlen=1000)` per source in the `_sources` dict to retain
  recent timing samples (follow the `NodeStateEntry.heartbeats` deque pattern from
  `src/utils/node_state.py:66`).
- Add a `_percentiles()` static method that sorts the buffer and picks p50/p90/p99
  by index. Stdlib-only -- no numpy.
- Add percentile fields to the `_format_source()` output (line 134) so the existing
  `/api/perf` endpoint automatically includes them.
- Add the same rolling window to `_cycle` stats.

**Tests:** `tests/test_perf_monitor.py` -- add percentile tests with known sample sets.

### Priority 2 -- Per-Endpoint Request Metrics

**Files:** `src/map_server.py`, `src/utils/perf_monitor.py`

The `MapRequestHandler.do_GET()` method routes all requests through `_ROUTE_TABLE`.
No per-endpoint timing or counting exists.

**Changes:**
- Add `record_request(path, duration_ms, status_code)` to `PerfMonitor`.
- In `do_GET()`, wrap handler dispatch in `time.monotonic()` timing (same approach as
  `TimingContext.__enter__/__exit__` in `perf_monitor.py:160-171`).
- Track per-path: request count, avg/p50/p90/p99 latency, status code distribution.
- Add `/api/perf/endpoints` endpoint (or extend `/api/perf` response).

**Tests:** `tests/test_map_server.py` -- assert request counts increment correctly.

### Priority 3 -- SLA/Uptime Per Source

**Files:** `src/utils/perf_monitor.py`, `src/collectors/base.py`

`BaseCollector` already tracks `_total_collections`, `_total_errors`,
`_last_success_time`, `_last_error_time` (line 216-220). The `health_info` property
(line 289) exposes these. But no availability percentage is computed.

**Changes:**
- Add uptime tracking to `PerfMonitor`: record success/failure timestamps per source.
- Compute `availability_pct = successful / total * 100` over rolling windows (1h, 24h).
- Track consecutive failures per source for degradation trending.
- Add `availability_pct` and `consecutive_failures` to `get_stats()` output.

### Priority 4 -- Slow Query Logging

**File:** `src/utils/analytics.py`

`HistoricalAnalytics` delegates to `NodeHistoryDB.execute_read()` (line 108 of
`node_history.py`). No query timing exists.

**Changes:**
- Wrap each `execute_read()` call in `time.monotonic()` timing.
- Log queries exceeding 100ms at `WARNING` level with query text and duration.
- Add a `_slow_queries` deque (bounded, 50 entries) to `HistoricalAnalytics`.
- Expose via `/api/perf` response or a new `/api/perf/slow-queries` endpoint.

### Priority 5 -- Memory Usage Snapshots

**Files:** `src/utils/perf_monitor.py`, `src/map_server.py`

No tracking of in-memory data structure sizes or database file sizes.

**Changes:**
- Track and expose in `/api/perf`:
  - `MQTTNodeStore._nodes` dict size (cap: `MAX_NODES = 10000` in `mqtt_subscriber.py`)
  - `NodeHistoryDB.observation_count` property (line 401 of `node_history.py`)
  - `NodeHealthScorer._scores` dict size (cap: `MAX_SCORED_NODES = 10000` in `health_scoring.py`)
  - `NodeStateTracker._nodes` dict size (cap: `MAX_TRACKED_NODES = 10000` in `node_state.py`)
  - `ConfigDriftDetector._snapshots` dict size (cap: `MAX_TRACKED_NODES = 10000` in `config_drift.py`)
  - `AlertEngine._history` list size (cap: `MAX_ALERT_HISTORY = 500` in `alert_engine.py`)
  - `maps_node_history.db` file size on disk
- Add a `get_memory_snapshot()` method accepting the relevant components from
  `MapServerContext`.

### Priority 6 -- Alert Escalation Chains

**File:** `src/utils/alert_engine.py`

`AlertEngine` has per-node cooldowns (`_cooldowns` dict, line 221) but no escalation.

**Changes:**
- Add `escalation_after` field (seconds) to `AlertRule` dataclass (line 62).
- Add `escalation_level` field to `Alert` dataclass (line 118).
- In `evaluate_node()`, check for unacknowledged alerts past their escalation window
  and re-fire at the next severity level.
- Keep existing cooldown mechanism -- escalation is orthogonal to cooldown.

**Tests:** `tests/test_alert_engine.py` -- test escalation timing and severity promotion.

### Priority 7 -- Network-Wide Error Rate

**File:** `src/utils/perf_monitor.py`

Only cumulative error counts exist. No per-window error rate.

**Changes:**
- Track error timestamps per source in a bounded deque.
- Add `error_rate(source, window_seconds)` returning errors/minute.
- Surface in `/api/perf` response.

### Priority 8 -- Capacity Planning Metrics

**Files:** `src/utils/perf_monitor.py`, `src/utils/analytics.py`

**Changes:**
- Track node count growth rate (nodes/hour) from `HistoricalAnalytics.network_growth()`.
- Track DB file size growth over time.
- Track collection cycle time trends (are cycles slowing as node count grows?).
- Expose via `/api/perf` or a new `/api/capacity` endpoint.

---

## Session 3: Data Durability

Only two data stores are persistent: `NodeHistoryDB` (SQLite) and config
(`settings.json`). Everything else -- config drift history, node state, event bus
events, MQTT node store, collector caches -- is lost on restart. This session hardens
existing persistence and introduces new durable stores for critical state.

### Data Durability Matrix (Current State)

| Component | Persistent? | Restart Loss | Durability |
|-----------|:-----------:|:------------:|:----------:|
| NodeHistoryDB | SQLite | None | Medium |
| Config (settings.json) | JSON | In-memory diffs | Medium |
| BaseCollector caches | In-memory | Complete | Low |
| ConfigDrift history | In-memory | Complete | Low |
| NodeState tracking | In-memory | Complete | Low |
| EventBus events | In-memory | Complete | Low |
| MQTT node store | In-memory | Re-sync needed | Low |
| Alert history | In-memory | Complete | Low |

### Priority 1 -- DB Integrity Check on Startup

**File:** `src/utils/node_history.py`

`_init_db()` (line 63) creates tables but runs no integrity check. A corrupted DB
silently returns empty results.

**Changes:**
- After opening the connection (line 68-73), run `PRAGMA integrity_check`.
- If it returns anything other than `"ok"`, copy the corrupt DB to
  `maps_node_history.db.corrupt.<timestamp>`, delete the original, and re-initialize.
- Log the corruption event at `CRITICAL` level.
- Apply the same pattern to any new SQLite databases created in this session.

### Priority 2 -- Atomic Config File Writing

**File:** `src/utils/config.py`

`MapsConfig.save()` (line 140) writes directly with `open(path, "w")`. A crash
mid-write corrupts the JSON.

**Changes:**
- Use `tempfile.NamedTemporaryFile(dir=parent_dir, delete=False)` to write to a
  temp file in the same directory.
- After successful write + flush, use `os.replace(temp_path, config_path)` for
  atomic replacement (POSIX guarantee).
- Maintain the existing umask logic (lines 146-151).
- Before each save, copy the current file to `settings.json.bak` (single-generation
  backup).

### Priority 3 -- Persistent ConfigDrift History

**File:** `src/utils/config_drift.py`

`ConfigDriftDetector` stores all data in-memory (`_snapshots`, `_drift_history`
at lines 77-78). All drift events are lost on restart.

**Changes:**
- Add a SQLite database at `get_data_dir() / "config_drift.db"`.
- Schema:
  ```sql
  CREATE TABLE drift_events (
      id INTEGER PRIMARY KEY,
      node_id TEXT NOT NULL,
      field TEXT NOT NULL,
      old_value TEXT,
      new_value TEXT,
      severity TEXT NOT NULL,
      timestamp REAL NOT NULL
  );
  CREATE TABLE node_snapshots (
      node_id TEXT PRIMARY KEY,
      snapshot_json TEXT NOT NULL,
      first_seen REAL NOT NULL,
      last_seen REAL NOT NULL
  );
  ```
- On startup, load snapshots from DB into the `_snapshots` dict (hot cache).
- On each drift detection, write to DB in addition to in-memory deque.
- Use WAL mode and `busy_timeout=5000` (same as `NodeHistoryDB`, line 72-73).

**Pattern to follow:** `NodeHistoryDB.__init__()` and `_init_db()` in
`src/utils/node_history.py` (lines 48-106).

### Priority 4 -- Persistent NodeState Snapshots

**File:** `src/utils/node_state.py`

`NodeStateTracker` is fully in-memory (`_nodes` dict, line 139). On restart, all
nodes appear as "new" and uptime history is lost.

**Changes:**
- Add a SQLite database at `get_data_dir() / "node_state.db"`.
- Schema:
  ```sql
  CREATE TABLE node_states (
      node_id TEXT PRIMARY KEY,
      state TEXT NOT NULL,
      first_seen REAL NOT NULL,
      last_seen REAL NOT NULL,
      heartbeat_count INTEGER NOT NULL,
      transition_count INTEGER NOT NULL
  );
  ```
- On startup, load from DB to restore state (prevents false "new" classification).
- Flush to DB on state transitions (throttled, not on every heartbeat -- same concept
  as `NodeHistoryDB._throttle_seconds`).
- Add retention/cleanup matching `NodeHistoryDB.prune_old_data()` pattern (line 375).

### Priority 5 -- EventBus Event Logging

**File:** `src/utils/event_bus.py`

`EventBus` has only counters (`_total_published`, `_total_delivered`,
`_total_errors` at lines 155-157). No event history is retained.

**Changes:**
- Add a `FileEventLogger` subscriber that writes events to a JSONL file at
  `get_data_dir() / "events.jsonl"`.
- Circular approach: rotate at 10MB (configurable), keep 2 rotated files.
- Register as a wildcard subscriber: `bus.subscribe(None, logger_callback)`.
- Serialize each `Event` as a single JSON line: `event_type`, `timestamp`, `source`,
  and a truncated summary of `data`.
- Optional/configurable -- disabled by default to avoid disk I/O on constrained
  devices (e.g., Raspberry Pi 2W).

### Priority 6 -- BaseCollector Persistent Cache

**File:** `src/collectors/base.py`

In-memory cache (`_cache` at line 212, `_cache_time` at line 213) is lost on restart.
The existing `UNIFIED_CACHE_PATH` at line 59 establishes a cache directory pattern
but is not used.

**Changes:**
- Add an optional `persistent_cache_path` parameter to `BaseCollector.__init__()`.
- On successful `_fetch()`, serialize cache to disk at
  `get_data_dir() / "cache" / f"{source_name}.json"`.
- On startup, if in-memory cache is empty and persistent cache exists and is less
  than `max_staleness` seconds old (e.g., 4 hours), load it.
- Add a `max_staleness` parameter beyond which even the persistent cache is discarded.
- Validate loaded cache is valid GeoJSON (has `"type": "FeatureCollection"` key).

### Priority 7 -- Database Auto-Vacuum

**File:** `src/utils/node_history.py`

No `auto_vacuum` PRAGMA set. After 30-day retention pruning via `prune_old_data()`
(line 375), freed pages are not reclaimed and the DB file grows indefinitely.

**Changes:**
- Set `PRAGMA auto_vacuum=INCREMENTAL` on first DB creation (must be set before any
  tables exist, so only applies to new databases).
- After `prune_old_data()` deletes rows, run `PRAGMA incremental_vacuum(100)` to
  reclaim up to 100 pages.
- For any new SQLite databases (config_drift.db, node_state.db), set
  `auto_vacuum=INCREMENTAL` from the start.

### Priority 8 -- Data Export/Backup System

**Files:** `src/map_server.py`, potentially `src/utils/backup.py`

The map server has `/api/export/nodes` and `/api/export/alerts` endpoints but no
database-level backup.

**Changes:**
- Add `/api/backup/create` endpoint (POST, API key required) that triggers SQLite
  `.backup()` on `maps_node_history.db` to a timestamped file.
- Store backups at `get_data_dir() / "backups/"` with automatic rotation (keep last N).
- Add `/api/export/all` endpoint bundling nodes, alerts, analytics, config drift,
  and node states into a single JSON export.

---

## Implementation Notes

- **No new dependencies:** All work uses stdlib modules (`sqlite3`, `tempfile`, `os`,
  `collections`, `threading`, `json`, `time`).
- **Path resolution:** Use `get_data_dir()` from `src/utils/paths.py` for all data
  paths -- never `Path.home()` directly (see Known Gotchas in CLAUDE.md).
- **SQLite pattern:** Follow `NodeHistoryDB` (`src/utils/node_history.py`) for all new
  databases: WAL mode, `busy_timeout=5000`, thread-safe lock, `_init_db()` method.
- **Testing:** Test files mirror source modules (`test_<module>.py`). All collectors
  tested with mocks -- no network needed. Run `pytest tests/ -v` after each priority.
- **Security:** Backup/export endpoints must require API key auth. Config file writes
  must preserve the `0o077` umask for MQTT credential protection.
