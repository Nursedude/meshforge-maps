# Session Notes

---

## Session 12: Health Scoring, Performance Profiling, AREDN Hardening, OpenHamClock Priority

**Date:** 2026-02-11
**Branch:** `claude/session-management-tasks-tjRE2`
**Scope:** Five features — per-node health scoring, frontend overlay enhancements, performance profiling, AREDN test hardening, OpenHamClock port priority swap
**Version:** 0.6.0-beta → 0.7.0-beta

### Context

Session continued from Session 11 (reliability hardening). User specified 5 tasks in priority order. HamClock legacy is no longer in active development — OpenHamClock is the successor.

### Changes Made

**New Modules:**

1. **NodeHealthScorer** (`src/utils/health_scoring.py`, ~340 lines):
   - Per-node composite health score (0-100) from 5 weighted components:
     - Battery (0-25): battery level and/or voltage (weighted split if both available)
     - Signal (0-25): SNR quality and hop distance (70/30 split if both available)
     - Freshness (0-20): time since last observation vs configurable thresholds
     - Reliability (0-15): connectivity state from NodeStateTracker (stable/new/intermittent/offline)
     - Congestion (0-15): channel utilization and TX air time (inverted — lower is better)
   - Handles sparse data: normalizes score based on available components only
   - Status labels: excellent (>=80), good (>=60), fair (>=40), poor (>=20), critical (<20)
   - Thread-safe cache with LRU eviction (max 10,000 nodes)
   - `score_node()`, `get_node_score()`, `get_all_scores()`, `get_summary()`, `remove_node()`

2. **PerfMonitor** (`src/utils/perf_monitor.py`, ~190 lines):
   - Collection cycle timing with per-source latency tracking
   - TimingContext context manager for clean instrumentation
   - Percentile stats (p50/p90/p99), cache hit ratio, collections/minute
   - Memory usage tracking (sample counts, tracked sources)
   - Bounded history (max 100 samples per source, configurable)
   - Integrated into DataAggregator.collect_all() for automatic instrumentation

**Enhanced Modules:**

3. **Frontend overlay enhancements** (`web/meshforge_maps.html`):
   - Node Health overlay toggle — color-codes markers by health score (excellent=green, poor=red)
   - Health badge in node popups showing score and status
   - VOACAP bands now show SNR dB values alongside reliability percentages
   - Panel title changed from "HamClock Propagation" to "Propagation"
   - Source indicator distinguishes "OpenHamClock" vs "HamClock" variant
   - Fallback message updated: "OpenHamClock unavailable" (was "HamClock unavailable")
   - CSS classes for health status badges (health-excellent/good/fair/poor/critical)
   - `rebuildMarkersFromFeatures()` for re-rendering with health colors without re-fetching

4. **OpenHamClock port priority swap** (`src/collectors/hamclock_collector.py`):
   - Port detection order reversed: OpenHamClock (3000) tried first, legacy (8080) as fallback
   - Rationale: HamClock legacy is no longer in active development
   - Log message updated for legacy fallback case
   - All existing behavior preserved (same-port skip, variant detection, endpoint mapping)

5. **DataAggregator instrumentation** (`src/collectors/aggregator.py`):
   - collect_all() now wrapped in PerfMonitor timing contexts
   - Per-source timing with node count and cache-hit detection
   - Full cycle timing with total node count
   - `perf_monitor` property exposed for API access

6. **MapServer wiring** (`src/map_server.py`):
   - 4 new API endpoints: `/api/node-health`, `/api/node-health/summary`, `/api/nodes/<id>/health`, `/api/perf`
   - NodeHealthScorer initialized in MapServer.__init__()
   - Health scorer attached to HTTP server for handler access
   - Eviction cleanup wired (node removed → health score removed)

### Test Coverage Added (132 new tests)

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_health_scoring.py` | 66 | All 5 components, composite scoring, normalization, cache, eviction, summary |
| `test_perf_monitor.py` | 20 | Timing recording, context manager, percentiles, stats, memory usage |
| `test_aredn_hardening.py` | 46 | Network failures, malformed JSON, cache file handling, coordinates, dedup, LQM edges, port detection |

### Test Results
- **Before:** 538 passed, 22 skipped (Session 11 baseline)
- **After:** 670 passed (+132 new), 22 skipped, 0 failures, 0 regressions

### Files Created (4)
- `src/utils/health_scoring.py` — Per-node health scoring module
- `src/utils/perf_monitor.py` — Performance monitoring module
- `tests/test_health_scoring.py` — 66 tests for health scoring
- `tests/test_perf_monitor.py` — 20 tests for perf monitoring
- `tests/test_aredn_hardening.py` — 46 tests for AREDN collector hardening

### Files Modified (6)
- `src/map_server.py` — Health scorer init, 4 new endpoints, eviction cleanup
- `src/collectors/aggregator.py` — PerfMonitor integration, instrumented collect_all()
- `src/collectors/hamclock_collector.py` — OpenHamClock port priority (3000 first, 8080 fallback)
- `web/meshforge_maps.html` — Health overlay, VOACAP SNR, OpenHamClock labels
- `tests/test_collectors.py` — Updated port order tests for OpenHamClock priority
- `manifest.json` — Version bump to 0.7.0-beta
- `src/__init__.py` — Version bump to 0.7.0-beta

### Architecture Notes
- Health scoring normalizes to 0-100 regardless of available data (avoids penalizing sparse nodes)
- PerfMonitor uses monotonic clock for timing, wall clock for timestamps
- Frontend health overlay fetches scores on-demand, not on every refresh
- AREDN collector _fetch() skips features without truthy ID (unlike aggregator which allows them)
- OpenHamClock priority swap is a one-line conceptual change but touches test expectations

### Session Entropy Watch
- Session stayed focused and systematic throughout
- All 5 tasks completed in priority order without scope creep
- No entropy detected — clean implementation boundaries per task
- Zero regressions at every checkpoint (538 → 604 → 624 → 670)

---

## Session 11: Reliability Hardening — Thread Safety, Input Validation, Resource Cleanup

**Date:** 2026-02-11
**Branch:** `claude/meshforge-reliability-features-TULsN`
**Scope:** Comprehensive reliability audit and fixes — thread safety, resource leaks, input validation, shutdown lifecycle, eviction cleanup
**Version:** 0.6.0-beta (no version bump — fixes only, no new features)

### Context

Full codebase audit identified 34 issues across severity levels (2 critical, 5 high, 13 medium, 14 low). This session addressed all critical and high severity issues plus key medium-severity items.

### Changes Made

**Critical Fixes:**

1. **Proxy request counter race condition** (`src/utils/meshtastic_api_proxy.py`):
   - `_request_count` was incremented without locks from concurrent HTTP handler threads
   - Added `_request_count_lock`, `_inc_request_count()` method, and `request_count` property
   - All 4 handler methods now use thread-safe increment
   - `stats` property uses thread-safe accessor

2. **Node history DB connection leak** (`src/utils/node_history.py`):
   - `_init_db()` opened a sqlite3 connection then set `self._conn = None` on failure without closing it
   - Refactored to use local `conn` variable; if schema creation fails, `conn.close()` is called before setting `self._conn = None`

**High Severity Fixes:**

3. **Node ID input validation** (`src/map_server.py`):
   - Added `_NODE_ID_RE` regex: `^!?[0-9a-fA-F]{1,16}$`
   - Added `_validate_node_id()` function
   - `/api/nodes/<id>/trajectory` and `/api/nodes/<id>/history` now return 400 for invalid IDs
   - Prevents injection via URL path parameters

4. **Safe query parameter extraction** (`src/map_server.py`):
   - Added `_safe_query_param(query, key, default)` helper
   - Handles missing keys, empty lists, empty string values safely
   - `_serve_trajectory()` and `_serve_node_history()` now use it
   - Invalid `since`/`until`/`limit` params return 400 instead of silently defaulting
   - `limit` parameter clamped to 1-10000 range

5. **MQTT subscriber thread leak on stop** (`src/collectors/mqtt_subscriber.py`):
   - `stop()` now joins the main subscriber thread with 5s timeout
   - Logs warning if thread doesn't exit
   - `disconnect()` and `loop_stop()` exceptions are now logged at debug level instead of silently swallowed

6. **WebSocket broadcast/history race condition** (`src/utils/websocket_server.py`):
   - `broadcast()` now holds the lock across both history append AND `call_soon_threadsafe()` scheduling
   - Prevents new clients from receiving duplicate messages
   - Added `RuntimeError` catch for event loop closed between check and call
   - `shutdown()` now catches `RuntimeError` from `loop.is_running()` race

**Medium Severity Fixes:**

7. **MapServer thread join on stop** (`src/map_server.py`):
   - `stop()` now joins the HTTP server thread with 5s timeout before releasing the port
   - Prevents "Address already in use" on rapid restart

8. **Proxy server thread join** (`src/utils/meshtastic_api_proxy.py`):
   - `stop()` now joins the proxy server thread with 5s timeout

9. **Node eviction cleanup propagation**:
   - `ConfigDriftDetector.remove_node(node_id)` — clears snapshot and drift history
   - `NodeStateTracker.remove_node(node_id)` — clears state entry
   - `MQTTNodeStore` now accepts `on_node_removed` callback
   - Both `_evict_oldest_locked()` and `cleanup_stale_nodes()` invoke the callback
   - `MapServer.__init__()` wires `_handle_node_removed()` to propagate eviction

10. **Bare except:pass replaced with logged exceptions** (6 locations):
    - `connection_manager.py` — lock release RuntimeError
    - `shared_health_state.py` — DB close
    - `node_history.py` — DB close, observation_count, node_count properties

### Test Coverage Added (28 new tests)

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_reliability_fixes.py` | 28 | Proxy thread safety, DB leak, validation, query params, MQTT stop, WS broadcast, MapServer stop, eviction cleanup |

### Test Results
- **Before:** 510 passed, 22 skipped
- **After:** 538 passed (+28 new), 22 skipped, 0 failures, 0 regressions

### Files Created (1)
- `tests/test_reliability_fixes.py` — 28 tests for all reliability fixes

### Files Modified (10)
- `src/utils/meshtastic_api_proxy.py` — Thread-safe request counter, thread join on stop
- `src/utils/node_history.py` — Connection leak fix, logged exceptions
- `src/utils/websocket_server.py` — Broadcast atomicity, shutdown race fix
- `src/utils/event_bus.py` — (reviewed, stats counting confirmed correct)
- `src/utils/config_drift.py` — `remove_node()` method
- `src/utils/node_state.py` — `remove_node()` method
- `src/utils/connection_manager.py` — Logged debug on lock release
- `src/utils/shared_health_state.py` — Logged debug on DB close
- `src/collectors/mqtt_subscriber.py` — Thread join, eviction callback
- `src/map_server.py` — Input validation, safe params, thread join, eviction wiring
- `tests/test_map_server.py` — Updated test node IDs to valid hex format

### Architecture Notes
- All thread join operations use 5-second timeout (consistent pattern)
- Node ID validation is hex-only (`!?[0-9a-fA-F]{1,16}`), matching Meshtastic format
- Eviction cleanup is callback-based (no event bus overhead for internal cleanup)
- Query parameter validation returns 400 errors (breaking change vs silent fallback, but correct)

### Session Entropy Watch
- Session stayed focused and systematic throughout
- No entropy detected — all fixes mapped to audit findings
- Clean boundary: all tests green, no new features, reliability-only changes

---

## Session 10: Meshtastic API Proxy, Health Telemetry, Config Drift, Node State Machine

**Date:** 2026-02-09
**Branch:** `claude/meshtastic-api-health-telemetry-b5b8v`
**Scope:** Four new features — Meshtastic API proxy, air quality/health telemetry parsing, config drift detection, node intermittent state machine
**Version:** 0.5.0-beta → 0.6.0-beta

### Changes Made

**New Modules:**

1. **MeshtasticApiProxy** (`src/utils/meshtastic_api_proxy.py`, ~290 lines):
   - HTTP server serving meshtasticd-compatible JSON REST API
   - Backed by live MQTTNodeStore — works without a local meshtasticd
   - `GET /api/v1/nodes` — all nodes as JSON array in meshtastic Python library format
   - `GET /api/v1/nodes/<node_id>` — single node by ID
   - `GET /api/v1/topology` — mesh topology links
   - `GET /api/v1/stats` — proxy statistics (uptime, request count, store state)
   - Port fallback: tries 5 consecutive ports (default 4404)
   - Thread-safe background server with proper shutdown
   - Node formatting: hex ID→int conversion, device/environment/air quality/health metrics
   - `set_store()` for late binding when MQTT subscriber starts after proxy

2. **ConfigDriftDetector** (`src/utils/config_drift.py`, ~200 lines):
   - Compares successive node config observations to detect changes
   - Tracks: role, hardware, name, short_name, region, modem_preset, hop_limit, tx_power, channel_name, uplink/downlink
   - Three severity levels: INFO (name changes), WARNING (role/hardware), CRITICAL (region/modem preset)
   - Per-node snapshot storage with drift history
   - `check_node(node_id, **fields)` — returns list of detected drifts
   - `get_all_drifts(since, severity)` — filtered drift query
   - `get_summary()` — overview for API consumption
   - Optional `on_drift` callback for real-time alerting
   - Bounded memory: max_history per node, max_nodes with LRU eviction

3. **NodeStateTracker** (`src/utils/node_state.py`, ~260 lines):
   - Four-state connectivity machine: NEW → STABLE → INTERMITTENT → OFFLINE
   - Driven by `record_heartbeat(node_id)` on each node observation
   - Sliding window of heartbeat timestamps (default 20)
   - Gap ratio analysis: fraction of intervals exceeding 2× expected interval
   - `check_offline(now)` — batch transition for nodes not seen within threshold
   - `get_nodes_by_state(state)` — filter all nodes by connectivity state
   - `get_summary()` — state counts and transition totals
   - Optional `on_transition` callback for state change notifications
   - Bounded memory: max_nodes with LRU eviction

**Enhanced Modules:**

4. **Air quality telemetry** (`src/collectors/mqtt_subscriber.py`):
   - `_handle_telemetry()` now parses `air_quality_metrics` protobuf:
     - PM1.0/2.5/4.0/10 (standard and environmental)
     - CO2 (ppm), VOC Index, NOx Index
   - `_handle_telemetry()` now parses `health_metrics` protobuf:
     - Heart rate (BPM), SpO2 (%), body temperature
   - `_handle_telemetry()` now parses IAQ from `environment_metrics`
   - `MQTTNodeStore.update_telemetry()` extended with `iaq` param and `**extra` kwargs
   - All new fields flow through to GeoJSON properties via `_parse_mqtt_node()`

5. **MeshtasticCollector** (`src/collectors/meshtastic_collector.py`):
   - `_parse_mqtt_node()` now passes through air quality (pm25, co2, voc, nox) and health (heart_bpm, spo2, body_temperature) fields to `make_feature()`

6. **MapServer** (`src/map_server.py`):
   - Creates ConfigDriftDetector, NodeStateTracker, MeshtasticApiProxy on init
   - Subscribes drift detector to NODE_INFO events via event bus
   - Subscribes state tracker to NODE_POSITION, NODE_TELEMETRY, NODE_INFO as heartbeats
   - Starts proxy server alongside HTTP and WebSocket servers
   - Proper cleanup: proxy.stop() in stop()
   - 5 new API endpoints:
     - `GET /api/config-drift` — drift detection summary
     - `GET /api/config-drift/summary` — drift detection summary
     - `GET /api/node-states` — all node connectivity states
     - `GET /api/node-states/summary` — state counts
     - `GET /api/proxy/stats` — Meshtastic API proxy statistics

### New API Endpoints

| Endpoint | Data | Source |
|----------|------|--------|
| `GET /api/config-drift` | Config drift summary | ConfigDriftDetector |
| `GET /api/config-drift/summary` | Drift counts and recent events | ConfigDriftDetector |
| `GET /api/node-states` | All node states + summary | NodeStateTracker |
| `GET /api/node-states/summary` | State counts | NodeStateTracker |
| `GET /api/proxy/stats` | Proxy uptime, requests, store state | MeshtasticApiProxy |

### Meshtastic API Proxy Endpoints (port 4404)

| Endpoint | Data | Format |
|----------|------|--------|
| `GET /api/v1/nodes` | All nodes | meshtastic Python library JSON |
| `GET /api/v1/nodes/<id>` | Single node | meshtastic JSON |
| `GET /api/v1/topology` | Mesh topology links | JSON |
| `GET /api/v1/stats` | Proxy statistics | JSON |

### Test Coverage Added (86 new tests)

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_meshtastic_api_proxy.py` | 21 | Formatting, lifecycle, all proxy endpoints, edge cases |
| `test_config_drift.py` | 25 | Severity levels, multi-field, history, eviction, callbacks |
| `test_node_state.py` | 25 | State transitions, gap ratio, offline, summary, eviction |
| `test_health_telemetry.py` | 10 | Air quality, health, IAQ, combined telemetry, None handling |
| `test_map_server.py` | +5 | New endpoint integration tests |

### Test Results
- **Before:** 424 passed, 22 skipped
- **After:** 510 passed (+86 new), 22 skipped, 0 failures, 0 regressions

### Architecture Notes
- MeshtasticApiProxy runs on port 4404 (adjacent to meshtasticd's 4403)
- ConfigDriftDetector fed by NODE_INFO events from MQTT subscriber via EventBus
- NodeStateTracker fed by all node events (position, telemetry, info) as heartbeats
- All new modules follow existing patterns: thread-safe, bounded memory, graceful degradation
- Air quality and health telemetry stored in MQTTNodeStore via **extra kwargs (flexible schema)
- meshtasticd's real API is binary protobuf only — the proxy serves JSON for easier tool integration

### Files Created (7)
- `src/utils/meshtastic_api_proxy.py` — Meshtastic API proxy server
- `src/utils/config_drift.py` — Config drift detection
- `src/utils/node_state.py` — Node intermittent state machine
- `tests/test_meshtastic_api_proxy.py` — 21 tests
- `tests/test_config_drift.py` — 25 tests
- `tests/test_node_state.py` — 25 tests
- `tests/test_health_telemetry.py` — 10 tests

### Files Modified (6)
- `src/__init__.py` — Version 0.5.0-beta → 0.6.0-beta
- `manifest.json` — Version 0.5.0-beta → 0.6.0-beta
- `src/collectors/mqtt_subscriber.py` — Air quality + health telemetry parsing, IAQ, **extra kwargs
- `src/collectors/meshtastic_collector.py` — Pass through air quality + health fields
- `src/map_server.py` — Proxy, drift, state machine integration + 5 new endpoints
- `tests/test_map_server.py` — 5 new endpoint integration tests

### Session Entropy Watch
- Session stayed focused and systematic throughout all 4 features
- No entropy detected — all features implemented, tested, and integrated
- Clean boundary: all tests green, code compiles, ready for next session

---

## Session 8: Phase 3 — Node History, Topology GeoJSON, Cross-Process Health

**Date:** 2026-02-08
**Branch:** `claude/node-history-offline-caching-xAOm4`
**Scope:** Phase 3 feature depth — node history DB, server-side topology SNR coloring, shared health state reader, service worker enhancements, AREDN LQM neighbor resolution
**Version:** 0.3.0-beta → 0.4.0-beta

### Changes Made

**New Modules:**

1. **NodeHistoryDB** (`src/utils/node_history.py`, ~300 lines):
   - SQLite-backed node position history with WAL mode
   - Throttled recording (configurable, default 60s per node)
   - `get_trajectory_geojson(node_id)` — returns GeoJSON LineString for node movement over time
   - `get_snapshot(timestamp)` — returns network state at a point in time (GeoJSON FeatureCollection)
   - `get_node_history(node_id)` — raw observation list
   - `get_tracked_nodes()` — list all nodes with observation counts
   - `prune_old_data()` — automatic retention cleanup (default 30 days)
   - Thread-safe with per-operation locking
   - Wired to event bus: position events from MQTT auto-record to history

2. **SharedHealthStateReader** (`src/utils/shared_health_state.py`, ~175 lines):
   - Read-only SQLite access to MeshForge core's `health_state.db`
   - WAL mode for non-blocking concurrent reads
   - `get_service_states()` — gateway/bridge/service health from core
   - `get_node_health()` — per-node health scores from core
   - `get_latency_percentiles()` — p50/p90/p99 delivery latency
   - `get_summary()` — combined health overview for API
   - `refresh()` — re-check DB availability (for late core startup)
   - Graceful degradation: returns empty data when core is not running

**Enhanced Modules:**

3. **Server-side topology SNR edge coloring** (`src/collectors/mqtt_subscriber.py`):
   - New `_classify_snr()` helper with 5-tier quality classification
   - New `MQTTNodeStore.get_topology_geojson()` — returns GeoJSON FeatureCollection with LineString features
   - Each edge includes: SNR value, quality tier label, hex color, source/target IDs
   - Tiers aligned with meshforge core: Excellent(>8)/Good(5-8)/Marginal(0-5)/Poor(-10-0)/Bad(<-10)
   - Colors: green → light green → yellow → orange → red (+ grey for unknown)

4. **AREDN LQM neighbor resolution** (`src/collectors/aredn_collector.py`):
   - `_parse_lqm_neighbor()` now fully parses LQM entries instead of returning None
   - Extracts: SNR, noise, quality, tx/rx quality, link type (RF/DTD/TUN)
   - Filters blocked links
   - New `get_topology_links()` method resolves neighbor coordinates from known nodes
   - Coordinate resolution: matches LQM neighbor names to queried node positions
   - Collector tracks `_lqm_links` and `_node_coords` for topology building

5. **Service worker enhancements** (`web/sw-tiles.js`):
   - New dedicated `API_CACHE` for API responses (separate from tile/static caches)
   - `PRECACHE_REGION` message handler for offline tile pre-caching
     - Accepts viewport bounds, tile URL template, zoom range
     - Rate-limited fetching (50ms delay between tiles)
     - Safety cap: 500 tiles per precache request, max zoom 14
     - `latLonToTile()` helper for coordinate-to-tile conversion
   - `CLEAR_ALL_CACHES` message for clearing tile + API caches
   - Enhanced `CACHE_STATS` returns tile, API, and static cache counts
   - Cache cleanup preserves all 3 named caches during activation

6. **New API endpoints** (`src/map_server.py`):
   - `GET /api/topology/geojson` — topology as GeoJSON FeatureCollection with SNR colors
   - `GET /api/nodes/<id>/trajectory?since=&until=` — node trajectory GeoJSON
   - `GET /api/nodes/<id>/history?since=&limit=` — node observation history
   - `GET /api/snapshot/<timestamp>` — historical network snapshot
   - `GET /api/history/nodes` — list all tracked nodes with observation counts
   - `GET /api/core-health` — MeshForge core shared health state

7. **MapServer lifecycle** (`src/map_server.py`):
   - Creates NodeHistoryDB and SharedHealthStateReader on init
   - Attaches both to HTTP server for handler access
   - Subscribes to NODE_POSITION events for automatic history recording
   - Proper cleanup on stop (close DB connections)
   - New `node_history` property on MapServer

### Test Results
- **Before:** 272 passed, 22 skipped
- **After:** 348 passed, 22 skipped, 0 failures
- **New tests:** 76 across 4 new test files + updates to 2 existing files
  - `test_node_history.py` — 26 tests (DB operations, trajectory GeoJSON, snapshots, pruning, closed DB safety)
  - `test_shared_health_state.py` — 14 tests (unavailable DB, service states, node health, latency, refresh, summary)
  - `test_topology_geojson.py` — 18 tests (SNR classification boundaries, GeoJSON feature structure, multi-link)
  - `test_aredn_lqm.py` — 14 tests (neighbor parsing, blocked links, DTD/TUN types, coordinate resolution)
  - `test_map_server.py` — +8 tests (new endpoint integration tests)
  - `test_collectors.py` — 1 updated test (LQM signature change)

### Architecture Notes
- NodeHistoryDB stored at `~/.local/share/meshforge/maps_node_history.db`
- SharedHealthStateReader reads `~/.config/meshforge/health_state.db` (core's DB)
- Topology GeoJSON aggregator merges MQTT + AREDN links with unified SNR coloring
- All new modules follow existing patterns: thread-safe, graceful degradation, logging
- Version bumped to 0.4.0-beta (significant new features)

### Files Created (4)
- `src/utils/node_history.py` — Node history SQLite DB
- `src/utils/shared_health_state.py` — Cross-process health reader
- `tests/test_node_history.py` — 26 tests
- `tests/test_shared_health_state.py` — 14 tests
- `tests/test_topology_geojson.py` — 18 tests
- `tests/test_aredn_lqm.py` — 14 tests

### Files Modified (6)
- `src/__init__.py` — Version 0.3.0-beta → 0.4.0-beta
- `src/collectors/mqtt_subscriber.py` — SNR classification, topology GeoJSON
- `src/collectors/aggregator.py` — Topology GeoJSON aggregation, AREDN link merging
- `src/collectors/aredn_collector.py` — LQM neighbor resolution, topology links
- `src/map_server.py` — 6 new API routes, NodeHistoryDB/SharedHealthState integration
- `web/sw-tiles.js` — Region precaching, API cache, enhanced stats
- `tests/test_map_server.py` — 8 new endpoint integration tests
- `tests/test_collectors.py` — Updated LQM test

### What Still Needs Work (Phase 4 Roadmap)
1. **Connection manager integration** for MeshtasticCollector
2. **Frontend trajectory visualization** — Leaflet polyline rendering for trajectory API
3. **Frontend topology GeoJSON rendering** — Switch from D3 to Leaflet GeoJSON layer using server-side colors
4. **Node history frontend panel** — UI for viewing tracked nodes and trajectory playback
5. **OpenHamClock API differences** — endpoint mapping if API diverges from original

### Session Entropy Notes
- Session stayed focused and systematic throughout all 5 Phase 3 items
- No entropy detected — all features implemented, tested, and integrated
- Clean boundary: all tests green, code compiles, ready for Phase 4

---

## Session 7: README Update -- Supported Hardware, OS, Version Sync

**Date:** 2026-02-08
**Branch:** `claude/update-readme-hardware-TtMOJ`
**Scope:** Documentation update -- add supported Raspberry Pi hardware and OS tables, fix stale version/test badges

### Changes Made

**README.md:**
1. **Version badge fix:** `0.2.0-beta` → `0.3.0-beta` (was out of sync with `manifest.json` and `src/__init__.__version__` since Session 4)
2. **Test count badge fix:** `111` → `272` (was stale since Session 1; sessions 2-6 added 161 tests)
3. **New "Supported Hardware" section:** Raspberry Pi compatibility matrix (Pi 5, Pi 4, Pi 400, Pi 3 B+, Pi 3 B, Zero 2 W, Zero W) with SoC, RAM, status, and deployment notes
4. **New "Supported Operating Systems" table:** RPi OS Bookworm/Bullseye, Ubuntu Server 22.04/24.04, DietPi, Armbian, Debian, macOS, Windows -- with Python versions and status
5. **Buster not-supported callout:** Debian 10 ships Python 3.7, below the 3.9 minimum
6. **Updated Testing section:** Expanded test coverage description to include circuit breaker, reconnect, event bus, WebSocket, OpenHamClock, and health endpoint tests

### Files Modified (1)
- `README.md` -- all changes above (43 insertions, 4 deletions)

### Test Results
- No code changes; test suite unchanged at 272 passed, 22 skipped

### Session Entropy Watch
Minimal session -- single focused documentation task. No entropy.

---

## Session 6: Reliability Hardening + meshforge Core Alignment

**Date:** 2026-02-08
**Branch:** `claude/improve-reliability-features-qgFyX`
**Scope:** Bug fixes, reliability improvements, meshforge core feature parity, OpenHamClock readiness

### Changes Made

**Bug Fixes:**
1. **ReconnectStrategy backoff bug (critical):** `BaseCollector.collect()` created a fresh `ReconnectStrategy.for_collector()` per retry attempt, so delays never escalated (always started at ~1s). Now creates a single strategy per retry loop.
2. **EventBus stats thread safety:** `_BusStats` counters (`total_published`, `total_delivered`, `total_errors`) were modified outside any lock by concurrent publishers. Now uses per-operation locking with `inc_published()` / `inc_delivered()` / `inc_errors()` methods and `@property` read accessors.
3. **Version string consistency:** Removed hardcoded `"0.3.0-beta"` from `map_server.py` and `"MeshForge-Maps/0.3"` User-Agent from `hamclock_collector.py`. Both now import from `src/__init__.__version__`.

**New Features:**
4. **OpenHamClock port 3000 auto-detection:** HamClock (WB0OEW, SK) ceases operation June 2026. OpenHamClock (MIT, port 3000) is the community successor. `HamClockCollector.is_hamclock_available()` now tries configured port first (default 8080), then falls back to OpenHamClock port (default 3000). Reports detected variant ("hamclock" or "openhamclock") in API responses.
5. **`/api/health` endpoint:** Composite health scoring (0-100) with three components:
   - Freshness (40 pts): data age vs cache TTL
   - Source availability (30 pts): proportion of sources returning data
   - Circuit breaker health (30 pts): proportion of CLOSED breakers
   - Maps score to status: healthy/fair/degraded/critical
6. **Graceful SIGTERM/SIGINT handling:** Standalone mode (`main.py`) now uses `signal.signal()` + `threading.Event.wait()` instead of `while True: sleep(1)`. Proper shutdown for systemd/Docker/containers.
7. **WebSocket port fallback:** Tries up to 5 adjacent ports (matching HTTP server fallback pattern) instead of giving up on first bind failure.
8. **5-tier SNR topology link colors:** Aligned with meshforge core `topology_visualizer.py` quality tiers (Excellent/Good/Marginal/Poor/Bad with green→red gradient). Link popups now show quality label.

**Frontend:**
9. Health check now uses `/api/health` instead of `/api/status` for degradation/critical alerts.

### Test Results
- **Before:** 264 passed, 22 skipped
- **After:** 272 passed, 22 skipped, 0 failures
- New tests: 7 OpenHamClock detection + 1 health endpoint = 8 new tests

### Architecture Notes
- `HamClockCollector` now tracks `_detected_variant` and `_openhamclock_port`
- `_BusStats` is now a proper thread-safe counter class (not a bare data holder)
- `MapRequestHandler` routes `/api/health` -> `_serve_health()` with weighted scoring
- Config `DEFAULT_CONFIG` now includes `openhamclock_port: 3000`

### Files Modified (8)
- `src/collectors/base.py` - ReconnectStrategy fix
- `src/collectors/hamclock_collector.py` - OpenHamClock support + version consistency
- `src/collectors/aggregator.py` - Pass openhamclock_port config
- `src/map_server.py` - /api/health endpoint + version import + WS port fallback
- `src/main.py` - Signal handling (SIGTERM/SIGINT)
- `src/utils/config.py` - openhamclock_port default
- `src/utils/event_bus.py` - Thread-safe _BusStats
- `web/meshforge_maps.html` - 5-tier link colors + health check
- `tests/test_collectors.py` - 7 new OpenHamClock tests
- `tests/test_map_server.py` - 1 new health endpoint test

### What Still Needs Work (Phase 3 Roadmap)
1. **Node history DB** with `get_trajectory_geojson()` - track movement over time
2. **Topology visualization** with full SNR-based edge coloring server-side (current is client-side only)
3. **Shared health state reader** for cross-process visibility (MeshForge core integration)
4. **Service worker** for offline tile caching (sw-tiles.js registered but not yet implemented)
5. **AREDN LQM neighbor resolution** - currently `_parse_lqm_neighbor` returns None
6. **OpenHamClock API differences** - may need endpoint mapping if API diverges from original

### Session Entropy Notes
- Session stayed focused and systematic throughout
- No entropy detected - stopping at a clean boundary with all tests green
- Next session should start from Phase 3 roadmap items above

---

## Session 5: HamClock API + TUI Improvements

**Date:** 2026-02-08
**Branch:** `claude/hamclock-api-tui-improvements-XzGWs`
**Scope:** Expand HamClock API coverage, add TUI tools, frontend propagation panel, per-source health

### What Was Done

#### Modified: `src/collectors/hamclock_collector.py`
- **New `_fetch_dxspots()`:** Parses DX cluster spots from `get_dxspots.txt` (indexed Spot0=call freq de utc format)
- **New `get_hamclock_data()`:** Public method for TUI tools and `/api/hamclock` — returns all HamClock data using cached collection
- **Updated `_fetch()`:** Now also fetches DX spots when HamClock is available; includes `dxspots` key in output

#### Modified: `src/collectors/base.py`
- **Health tracking:** Added `_last_error`, `_last_error_time`, `_last_success_time`, `_total_collections`, `_total_errors` to BaseCollector
- **New `health_info` property:** Returns per-collector health dict (success counts, error info, cache status)
- Updated `collect()` to track success/error timestamps and counts

#### Modified: `src/collectors/aggregator.py`
- **New `get_source_health()`:** Returns per-source health info from all collectors' `health_info` property

#### Modified: `src/map_server.py`
- **New `/api/hamclock` route:** Dedicated endpoint serving all HamClock data (propagation, bands, DE/DX, DX spots)
- **New `_serve_hamclock()`:** Handler that calls `get_hamclock_data()` on the HamClock collector directly
- **Updated `/api/status`:** Now includes `source_health` dict with per-collector health info

#### Modified: `src/main.py`
- **3 new TUI tools registered on activate:**
  - `meshforge_maps_propagation` — Shows HF propagation conditions (SFI, Kp, VOACAP bands, band conditions)
  - `meshforge_maps_dxspots` — Shows recent DX cluster spots in formatted table
  - `meshforge_maps_hamclock_status` — Shows HamClock connection status, DE/DX, spot count, circuit breaker state
- **New methods:** `_get_propagation()`, `_get_dxspots()`, `_get_hamclock_status()`

#### Modified: `web/meshforge_maps.html`
- **New HamClock Propagation panel section:**
  - DE/DX station info (callsign + grid square)
  - VOACAP band prediction bars (80m-10m with color-coded reliability %)
  - Band conditions display from HamClock
  - DX Spots list (latest 10 with call, freq, DE, UTC)
  - Source indicator (HamClock API active vs NOAA fallback)
- **New CSS:** VOACAP bar charts, station info boxes, DX spot list, source indicator styles
- **New JS:** `loadHamClockData()` fetches from `/api/hamclock`, `updateHamClockPanel()` renders all sections
- Panel auto-loads on successful node data fetch, hidden when HamClock unavailable

#### Modified: Tests
- **test_collectors.py:** +9 tests (DX spots parsing, get_hamclock_data, updated _fetch tests, source_health)
- **test_base.py:** +3 tests (health_info initial, after success, after error)
- **test_plugin.py:** +9 tests (propagation, dxspots, hamclock_status TUI tools, tool registration count)
- **test_map_server.py:** +2 tests (/api/hamclock 404 when disabled, source_health in status)

### Test Results
- **Before:** 243 passed, 22 skipped
- **After:** 264 passed (+21 new), 22 skipped, 0 regressions

### New API Endpoints
| Endpoint | Data | Source |
|----------|------|--------|
| `/api/hamclock` | Full HamClock data (weather, VOACAP, bands, DE/DX, spots) | HamClock collector |

### New TUI Tools
| Tool ID | Name | Description |
|---------|------|-------------|
| `meshforge_maps_propagation` | HF Propagation | SFI, Kp, VOACAP bands, band conditions |
| `meshforge_maps_dxspots` | DX Spots | Recent DX cluster spots table |
| `meshforge_maps_hamclock_status` | HamClock Status | Connection, DE/DX, spots, circuit breaker |

### Session Entropy Watch
Session focused. Systematic task list (9 items, all completed). Zero regressions. No scope creep.

### Next Session Suggestions
1. **OpenHamClock port 3000:** Add auto-detection for OpenHamClock alongside HamClock legacy (port 8080 vs 3000)
2. **Node history DB:** Phase 3 from roadmap — SQLite node position history for track visualization
3. **Health scoring:** Per-node health score based on battery, SNR, channel utilization
4. **Frontend topology enhancement:** D3.js force graph improvements, link quality legend

---

## Session 4: HamClock API-Only Refactor

**Date:** 2026-02-08
**Branch:** `claude/hamclock-api-only-rF99v`
**Scope:** Refactor HamClockCollector to API-only architecture, aligned with meshforge core

### Context

Nursedude/meshforge underwent a major HamClock decoupling (see `.claude/research/hamclock_decoupling.md`):
- **Before:** NOAA SWPC was primary, HamClock was a minor optional add-on
- **After (meshforge core):** NOAA SWPC primary via `commands.propagation`, HamClock/OpenHamClock as optional REST API enhancement
- **After (meshforge-maps):** HamClock REST API is PRIMARY when running, NOAA SWPC is FALLBACK

Original HamClock (WB0OEW, SK) sunsets June 2026. OpenHamClock (MIT, port 3000) is the community successor.

### What Was Done

#### Modified: `src/collectors/hamclock_collector.py` (complete rewrite)
- **Architecture:** HamClock REST API primary, NOAA SWPC fallback
- **New `_fetch_text()` method:** Fetches raw text responses from HamClock
- **New `_parse_key_value()` function:** Parses HamClock's `key=value` text format
- **New `is_hamclock_available()`:** Tests connectivity via `get_sys.txt`
- **New `_fetch_space_weather_hamclock()`:** Gets SFI, Kp, A, X-ray, SSN, proton, aurora via `get_spacewx.txt`
- **New `_fetch_band_conditions_hamclock()`:** Gets HF band conditions via `get_bc.txt`
- **New `_fetch_voacap()`:** Gets VOACAP propagation predictions via `get_voacap.txt` with band reliability/SNR parsing
- **New `_fetch_de()` / `_fetch_dx()`:** Gets home/target location via `get_de.txt` / `get_dx.txt`
- **Renamed `_fetch_space_weather()` → `_fetch_space_weather_noaa()`:** NOAA fallback path, preserves original SWPC logic
- **New `_reliability_to_status()`:** Maps VOACAP reliability % → excellent/good/fair/poor/closed
- **`_fetch()` now branches:** Checks HamClock availability first, falls back gracefully
- **Removed:** `SWPC_A_INDEX`, `SWPC_XRAY_FLUX` constants (unused in NOAA fallback path)
- **Removed:** `HAMCLOCK_API` constant (replaced by instance `_hamclock_api`)

#### Modified: `src/utils/config.py`
- Added `hamclock_host` (default: "localhost") and `hamclock_port` (default: 8080) to `DEFAULT_CONFIG`

#### Modified: `src/collectors/aggregator.py`
- Passes `hamclock_host` and `hamclock_port` from config to `HamClockCollector` constructor

#### Modified: `tests/test_collectors.py`
- 26 HamClock tests (8 original + 18 new):
  - Constructor defaults and custom host/port
  - `_parse_key_value()` parsing (normal, empty, equals-in-value)
  - `is_hamclock_available()` true/false paths
  - HamClock API space weather parsing with key mapping
  - VOACAP band parsing (reliability, SNR, best band calculation)
  - Band conditions parsing from HamClock
  - DE/DX location parsing
  - Full `_fetch()` with HamClock available (all API endpoints called)
  - Full `_fetch()` with HamClock down (NOAA fallback path)
  - `_reliability_to_status()` edge cases

#### Modified: `tests/test_config.py`
- Added `test_hamclock_host_port_defaults` — verifies new config keys

### Test Results
- **Before:** 224 passed, 22 skipped
- **After:** 243 passed (+19 new), 22 skipped, 0 regressions

### Data Flow (New)
```
HamClock/OpenHamClock running?
    |
    ├── YES → get_spacewx.txt → space_weather (SFI, Kp, A, X-ray, SSN)
    │         get_bc.txt       → band_conditions (80m-10m)
    │         get_voacap.txt   → VOACAP predictions (reliability%, SNR)
    │         get_de.txt       → DE home station (lat, lon, grid, call)
    │         get_dx.txt       → DX target (lat, lon, grid, call)
    │
    └── NO  → NOAA SWPC APIs  → space_weather (SFI, Kp, solar wind)
              (automatic fallback, always works)

Both paths → _calculate_solar_terminator() (local computation)
          → FeatureCollection with overlay properties
```

### HamClock REST API Endpoints Used
| Endpoint | Data | Format |
|----------|------|--------|
| `get_sys.txt` | Version, uptime, DE/DX | key=value |
| `get_spacewx.txt` | SFI, Kp, A, X-ray, SSN, proton, aurora | key=value |
| `get_bc.txt` | HF band conditions (80m-10m) | key=value |
| `get_voacap.txt` | VOACAP propagation (per-band reliability + SNR) | key=value |
| `get_de.txt` | Home location (lat, lon, grid, callsign) | key=value |
| `get_dx.txt` | DX target (lat, lon, grid, callsign) | key=value |

### Design Decisions
- **HamClock primary, NOAA fallback:** Aligns with meshforge core's architecture where HamClock provides richer data (VOACAP, band conditions, DE/DX) than raw NOAA
- **Availability check per collection cycle:** `is_hamclock_available()` calls `get_sys.txt` once per `_fetch()`, caching the result
- **Key mapping is case-insensitive:** HamClock may return `SFI`, `sfi`, or `Flux` — the mapper handles all variants
- **NOAA fallback preserves original behavior:** If HamClock is down, maps works exactly as before with SWPC data
- **Backward compatible:** Constructor signature unchanged, new params have defaults

### Session Entropy Watch
Session focused. Systematic task list (7 items, all completed). Zero regressions.

### Next Session Suggestions
1. **Phase 3 features** from roadmap: Node history DB, health scoring, topology visualization
2. **OpenHamClock integration:** Add OpenHamClock (port 3000) as a third data source option alongside HamClock legacy and NOAA
3. **Frontend overlay enhancements:** Display VOACAP data and band conditions in the map UI when HamClock is available

---

## Session 3: Phase 2 Real-Time Architecture Implementation

**Date:** 2026-02-08
**Branch:** `claude/session-entropy-monitoring-b9PJx`
**Scope:** Implement Phase 2 (Real-Time Architecture) from the roadmap below

### What Was Done

#### New Modules Created
1. **`src/utils/event_bus.py`** -- EventBus + typed events (NodeEvent, ServiceEvent)
   - Thread-safe pub/sub with EventType-based filtering
   - Wildcard subscriptions (subscribe to all events)
   - Error isolation: `_safe_call()` wraps every callback
   - Stats tracking: published, delivered, errors
   - Factory methods: `NodeEvent.position()`, `.info()`, `.telemetry()`, `.topology()`
   - Factory methods: `ServiceEvent.up()`, `.down()`, `.degraded()`

2. **`src/utils/websocket_server.py`** -- MapWebSocketServer
   - Async WebSocket broadcast server (uses `websockets` library, optional dependency)
   - Runs in background thread with own asyncio event loop
   - 50-message history buffer for newly-connected clients
   - Thread-safe `broadcast()` callable from any thread (MQTT callbacks, etc.)
   - Stats: clients_connected, total_connections, total_messages_sent
   - Graceful fallback if `websockets` not installed

#### Modules Modified
3. **`src/collectors/mqtt_subscriber.py`** -- Event bus integration
   - New optional `event_bus` parameter (backward-compatible)
   - `_notify_update()` now publishes typed events to the bus
   - Added `_emit_event()` method mapping update_type -> NodeEvent factory
   - Added `_notify_update()` calls to ALL handlers (was only position before):
     - `_handle_nodeinfo` -> emits `node.info` with long_name, short_name
     - `_handle_telemetry` -> emits `node.telemetry`
     - `_handle_neighborinfo` -> emits `node.topology` with neighbor_count
     - `_handle_position` -> now includes lat/lon in event

4. **`src/collectors/aggregator.py`** -- EventBus creation and wiring
   - Creates `EventBus` instance in `__init__`
   - Passes event_bus to `MQTTSubscriber` constructor
   - Exposes `event_bus` property for MapServer access

5. **`src/map_server.py`** -- WebSocket server lifecycle + event bridge
   - Creates `MapWebSocketServer` on adjacent port (http_port + 1)
   - Subscribes to event bus, forwards all events as JSON to WebSocket clients
   - `_forward_to_websocket()` serializes Event -> JSON with type, node_id, lat/lon
   - `/api/status` now includes `websocket` and `event_bus` stats
   - `/api/config` now includes `ws_port` for frontend discovery
   - `stop()` shuts down WebSocket server alongside HTTP

6. **`web/meshforge_maps.html`** -- WebSocket client
   - `connectWebSocket(port)` with exponential backoff reconnect (2s-30s)
   - Auto-connects after `/api/config` returns ws_port
   - `handleRealtimeMessage()` processes node.position events
   - `updateOrAddNode()` moves existing markers or adds temporary new ones
   - Polling continues as fallback (5-minute interval unchanged)
   - Connection status indicator shows "Live" when WS connected

#### Tests Written
7. **`tests/test_event_bus.py`** -- 26 tests
   - Event construction (NodeEvent, ServiceEvent factories)
   - Subscribe/publish (basic, multi-subscriber, type filtering, wildcard)
   - Unsubscribe (specific, wildcard, nonexistent)
   - Error isolation (bad subscriber doesn't break others)
   - Stats and subscriber counting
   - Thread safety (concurrent publish/subscribe, subscribe during publish)

8. **`tests/test_websocket_server.py`** -- 15 tests
   - Server lifecycle (start, stop, double start)
   - Client connections (connect, disconnect, multiple clients)
   - Broadcast (single client, multiple clients, no clients, not running)
   - History buffer (new client catchup, max size cap)
   - Stats counting (connections, messages)
   - Optional dependency handling

9. **`tests/test_realtime.py`** -- 16 tests
   - Aggregator creates EventBus and passes to MQTT subscriber
   - MQTT _notify_update emits correct typed events
   - Both legacy callback and event bus fire simultaneously
   - Full pipeline: EventBus -> WebSocket broadcast
   - End-to-end: MQTTSubscriber -> EventBus -> WebSocket client
   - MapServer starts WebSocket on adjacent port
   - /api/status includes websocket and event_bus stats
   - /api/config includes ws_port
   - Events published on aggregator bus reach WebSocket clients

### Test Results
- **Before:** 189 tests passing
- **After:** 246 tests passing (+57 new, 0 regressions)

### Design Decisions
- **Backward compatible:** event_bus parameter is optional (defaults to None)
- **Optional websockets dependency:** Follows same pattern as paho-mqtt (graceful fallback)
- **Event bus is synchronous:** Callbacks run in publisher's thread. MQTT subscriber publishes on its thread; bus forwards to WS server's thread-safe broadcast().
- **History buffer for late joiners:** New WebSocket clients get the last 50 messages on connect, so they see recent node activity immediately.
- **Polling preserved alongside WebSocket:** Frontend keeps 5-minute polling as fallback. WebSocket adds real-time layer on top.
- **Adjacent port convention:** WS runs on http_port + 1 (e.g., 8809 alongside 8808)
- **All handlers now notify:** Previously only position handler called _notify_update. Now all 4 handlers emit events.

### Data Flow (New)
```
MQTT Broker
    |
    v
MQTTSubscriber._handle_position()
    |
    ├── MQTTNodeStore.update_position()     (in-memory storage)
    ├── _notify_update("position", lat, lon)
    |       |
    |       ├── on_node_update callback     (legacy, optional)
    |       └── EventBus.publish(NodeEvent.position(...))
    |               |
    |               └── MapServer._forward_to_websocket()
    |                       |
    |                       └── MapWebSocketServer.broadcast(JSON)
    |                               |
    |                               └── WebSocket clients (real-time)
    v
DataAggregator.collect_all()              (polling, every 5 min)
    |
    v
/api/nodes/geojson                        (HTTP polling)
    |
    v
Frontend Leaflet Map                      (renders markers)
```

### Session Entropy Watch
Session remained focused and systematic. No entropy detected:
- Clear task list maintained and tracked (10 items, all completed)
- Each module implemented, tested, then integrated
- Zero regressions at every checkpoint (189 -> 230 -> 246)
- Consistent architecture decisions throughout

### Next Session: Phase 3 (Feature Depth)
1. Node history DB with `get_trajectory_geojson()`
2. Health scoring with `/api/health` endpoint
3. Topology visualization with SNR-based edge coloring
4. Shared health state reader for cross-process visibility

---

## Session 2: Phase 1 Reliability Foundation Implementation

**Date:** 2026-02-08
**Branch:** `claude/session-notes-setup-lFNhH`
**Scope:** Implement Phase 1 (Reliability Foundation) from the roadmap below

### What Was Done

#### New Modules Created
1. **`src/utils/circuit_breaker.py`** -- CircuitBreaker + CircuitBreakerRegistry
   - Per-source CLOSED/OPEN/HALF_OPEN state machine
   - Configurable failure_threshold (default 5) and recovery_timeout (default 60s)
   - Thread-safe (all state behind Lock)
   - Full stats: total_successes, total_failures, total_rejected, timestamps
   - Registry for named breakers with lazy creation

2. **`src/utils/reconnect.py`** -- ReconnectStrategy
   - Exponential backoff: `base * multiplier^attempt`
   - Jitter: `uniform(0, delay * jitter_factor)` to prevent thundering herd
   - Factory methods: `.for_mqtt()` (2s-120s, unlimited) and `.for_collector()` (1s-10s, 3 retries)
   - Attempt tracking persists across resets for diagnostics

#### Modules Modified
3. **`src/collectors/base.py`** -- BaseCollector enhanced
   - Optional `circuit_breaker` parameter (backward-compatible, defaults to None)
   - Optional `max_retries` parameter (default 0 = no retries, preserving old behavior)
   - Retry loop with backoff before cache fallback
   - Circuit breaker check before fetch (OPEN -> skip, return cache)
   - Records success/failure on circuit breaker after all retries exhausted

4. **`src/collectors/aggregator.py`** -- DataAggregator wired up
   - Creates `CircuitBreakerRegistry` with default thresholds
   - Assigns a circuit breaker to each enabled collector
   - Sets `max_retries=2` on all collectors
   - Exposes `get_circuit_breaker_states()` for API consumption

5. **`src/collectors/mqtt_subscriber.py`** -- MQTT reconnect upgraded
   - Replaced inline backoff math with `ReconnectStrategy.for_mqtt()`
   - Removed `import random` (now handled by strategy)
   - Logs attempt number for diagnostics

6. **`src/map_server.py`** -- `/api/status` enriched
   - New `circuit_breakers` field with per-source stats

#### Tests Written
7. **`tests/test_circuit_breaker.py`** -- 26 tests
   - State transitions (CLOSED->OPEN->HALF_OPEN->CLOSED)
   - Reset behavior, stats tracking, thread safety (concurrent operations)
   - Registry: creation, dedup, get_all_states, reset_all

8. **`tests/test_reconnect.py`** -- 19 tests
   - Exponential growth, max cap, jitter bounds
   - Attempt counting, retry limits, reset behavior
   - Factory methods (mqtt vs collector presets)

9. **`tests/test_reliability.py`** -- 19 tests
   - BaseCollector + circuit breaker integration
   - BaseCollector + retry integration (with time.sleep mocked)
   - DataAggregator + registry wiring

### Test Results
- **Before:** 125 tests passing
- **After:** 189 tests passing (+64 new, 0 regressions)

### Design Decisions
- **Backward compatible:** All new params are optional with defaults that preserve old behavior
- **Circuit breaker is external to retry:** CB check happens before retry loop. CB failure recorded only after all retries exhausted. This prevents a flaky-but-recoverable source from tripping the circuit.
- **Fresh ReconnectStrategy per collect():** Each collect() creates a new strategy for retry backoff. The MQTT subscriber uses a persistent strategy across its connection loop.
- **No sleep in tests:** All retry tests mock `time.sleep` to keep suite fast (8.75s total)

### Session Entropy Watch
Session remained focused and systematic. No entropy detected:
- Clear task list maintained throughout
- Each module implemented, tested, then committed
- Zero regressions at every checkpoint

### Next Session: Phase 2 (Real-Time Architecture)
1. WebSocket server on adjacent port
2. MQTT subscriber events -> WebSocket broadcast
3. Frontend WebSocket client alongside polling
4. Event bus for decoupled collector-to-server communication

---

## Session 1: MeshForge Core Feature & Reliability Analysis

**Date:** 2026-02-08
**Branch:** `claude/analyze-meshforge-features-Wf1G7`
**Scope:** Analyze Nursedude/meshforge (v0.5.3-beta, 3861 tests) for features and reliability patterns that benefit meshforge-maps (v0.3.0-beta, 111 tests)

---

## Executive Summary

MeshForge core has evolved significantly beyond meshforge-maps' current integration points. The core now has **14 key systems** that meshforge-maps could adopt or integrate with to improve reliability, real-time responsiveness, and feature depth. The highest-impact opportunities are: **WebSocket real-time push**, **circuit breaker per collector**, **node trajectory tracking**, and **shared health state**.

---

## MeshForge Core: Key Statistics

| Metric | Value |
|--------|-------|
| Version | 0.5.3-beta |
| Tests | 3,861 passing |
| Source files | 110+ Python modules |
| Lines of code | ~51,577 |
| TUI mixins | 35 feature modules |
| API endpoints | 42+ across 4 HTTP servers |
| Dependencies | meshtastic, rns, lxmf, rich, pyyaml, requests, psutil, folium, websockets |

---

## Feature Analysis: What MeshForge Core Has That Maps Needs

### TIER 1: High Priority (Direct Impact on Map Reliability & UX)

#### 1. Circuit Breaker Pattern (`gateway/circuit_breaker.py`)
**What it is:** Per-destination failure protection (Netflix Hystrix-style). When a destination accumulates consecutive failures, the circuit "opens" to stop requests, then periodically tests for recovery.

**Key classes:** `CircuitBreakerRegistry`, `CircuitBreaker` (CLOSED/OPEN/HALF_OPEN states), `CircuitStats`

**Why maps needs this:**
- Each of our 4 collectors (Meshtastic, Reticulum, AREDN, HamClock) hits external services
- A downed HamClock server currently causes timeout cascading into the aggregation pipeline
- The MQTT subscriber hammers an unreachable broker endlessly

**Implementation approach:**
- Wrap `BaseCollector._fetch()` with `registry.can_send(source_name)` / `record_success()` / `record_failure()`
- Expose circuit states in `/api/status` response
- Configurable thresholds: `failure_threshold=5`, `recovery_timeout=60s`

**Complexity:** Medium | **Impact:** High

---

#### 2. Exponential Backoff with Jitter (`gateway/reconnect.py`)
**What it is:** Reconnection strategy with interruptible waits, exponential backoff, jitter, and slow-start recovery.

**Key classes:** `ReconnectStrategy`, `ReconnectConfig`, `SlowStartRecovery`

**Why maps needs this:**
- MQTT subscriber has basic reconnect but no jitter (thundering herd risk)
- Collectors don't retry at all before falling back to cache
- No slow-start after reconnection (broker overload risk)

**Implementation approach:**
- Create factory methods: `ReconnectStrategy.for_mqtt()`, `.for_hamclock()`, etc.
- Add `execute_with_retry()` to `BaseCollector.collect()` (2-3 retries before cache fallback)
- Apply `SlowStartRecovery` to MQTT subscriber after reconnection

**Complexity:** Low | **Impact:** High

---

#### 3. WebSocket Real-Time Push (`utils/websocket_server.py`)
**What it is:** A WebSocket broadcast hub that solves the "single client" meshtasticd limitation. Runs in a background thread with its own asyncio event loop.

**Key classes:** `MessageWebSocketServer`, `WebSocketStats`

**Why maps needs this:**
- Map frontend currently polls `/api/nodes/geojson` periodically
- No instant node updates - lag between node appearance and map display
- Core map server (port 5000) already uses this pattern

**Implementation approach:**
- Add WebSocket server on port 8809 (adjacent to HTTP on 8808)
- Push node updates when MQTT subscriber receives new data
- Frontend connects to `ws://host:8809`, updates markers without polling
- 50-message history buffer for newly-connected clients

**Complexity:** Medium | **Impact:** Very High (transforms UX from polling to real-time)

---

#### 4. Event Bus (`utils/event_bus.py`)
**What it is:** Thread-safe pub/sub system with typed events (`NodeEvent`, `ServiceEvent`, `MessageEvent`).

**Key classes:** `EventBus` (singleton), `NodeEvent` (with lat/lon/node_id), `ServiceEvent`

**Why maps needs this:**
- Replace polling aggregation with event-driven updates
- MQTT subscriber could emit `NodeEvent` as nodes arrive
- Collectors could signal up/down via `ServiceEvent`
- Enables incremental feature collection updates

**Implementation approach:**
- MQTT subscriber emits `NodeEvent` on each decoded packet
- Aggregator maintains incrementally-updated FeatureCollection
- ServiceEvent signals collector health to `/api/status`
- `_safe_call()` wraps every callback (one bad subscriber never breaks others)

**Complexity:** Medium | **Impact:** High

---

### TIER 2: Medium Priority (Feature Depth & Operational Visibility)

#### 5. Health Scoring System (`utils/health_score.py`)
**What it is:** Synthesizes connectivity, performance, reliability, and freshness signals into a composite 0-100 score with trend detection.

**Key classes:** `HealthScorer`, `HealthSnapshot`, score-to-status mapping (healthy/fair/degraded/critical)

**Why maps needs this:**
- `/api/status` currently returns basic boolean `data_stale`
- No per-source health scoring or trend direction
- No signal quality visualization (SNR/RSSI scoring)

**Implementation approach:**
- Track each collector source as a scored service
- Expose via enriched `/api/status`: `overall_score`, per-source scores, trend direction
- Use `_snr_to_score()` and `_rssi_to_score()` for node popup quality badges
- Drive node marker color/opacity from freshness score (green/yellow/orange/red)

**Complexity:** Medium | **Impact:** Medium

---

#### 6. Node History & Trajectories (`utils/node_history.py`)
**What it is:** SQLite database recording node observations over time. Enables trajectory playback, historical snapshots, and growth tracking.

**Key classes:** `NodeHistoryDB`, `NodeObservation`, methods: `get_trajectory_geojson()`, `get_snapshot()`

**Why maps needs this:**
- No "where has this node been?" capability
- No historical network replay
- No growth/decline statistics

**Implementation approach:**
- Feed GeoJSON features from collection cycle into `record_observations()`
- New endpoint: `/api/nodes/<id>/trajectory` returning GeoJSON LineString
- Future: time slider for historical network state replay via `get_snapshot()`
- Throttled recording: 1 observation per node per minute, 7-day retention

**Complexity:** Medium | **Impact:** Medium (trajectory is a compelling visual feature)

---

#### 7. Shared Health State (`utils/shared_health_state.py`)
**What it is:** SQLite-backed cross-process health database. Gateway, TUI, and map server can all read/write atomically. WAL mode for concurrent reads.

**Key classes:** `SharedHealthState`, `ServiceHealthRecord`, `HealthState` enum, `HealthEvent`

**Why maps needs this:**
- Maps runs as a separate process from gateway/TUI
- No visibility into gateway bridge status or service health
- Could read `~/.config/meshforge/health_state.db` directly

**Implementation approach:**
- Read shared health DB on `/api/status` requests
- Surface gateway bridge state (HEALTHY/DEGRADED/OFFLINE) in map header
- Show latency percentiles (p50/p90/p99) for underlying services
- Display state transition history as timeline

**Complexity:** Low (read-only consumer) | **Impact:** Medium

---

#### 8. Topology Visualization (`utils/topology_visualizer.py`)
**What it is:** Multi-format network topology export (D3.js, GeoJSON, GraphML, CSV, ASCII) with SNR-based link quality coloring and node type classification.

**Key classes:** `TopologyVisualizer`, `TopoNode`, `TopoEdge`, `NODE_COLORS`, `NODE_SIZES`

**Why maps needs this:**
- Maps already has `/api/topology` but basic link data only
- No SNR-based edge coloring
- Color scheme could be aligned with core for visual consistency
- GeoJSON export includes simplestyle properties for direct Leaflet rendering

**Implementation approach:**
- Adopt `get_quality_color()` 5-tier SNR scale for topology link overlay
- Align `NETWORK_COLORS` with core's `NODE_COLORS` scheme
- Use `export_geojson()` format with simplestyle for Leaflet `L.geoJSON()` styling
- Consider embedded D3 force graph panel alongside geographic view

**Complexity:** Low | **Impact:** Medium

---

### TIER 3: Lower Priority (Architecture & Integration Polish)

#### 9. Connection Manager (`utils/connection_manager.py`)
- Solves the single-client meshtasticd TCP limitation
- `MeshtasticConnection` context manager with lock, retry, and cache fallback
- Maps' `MeshtasticCollector` should use this to avoid connection conflicts with host app

#### 10. Tile Cache (`utils/tile_cache.py`)
- Server-side tile downloading and caching for offline use
- `download_region()` with bounding box, zoom range, progress callbacks
- Complements maps' existing service worker tile cache with server-side pre-caching
- `BoundingBox` dataclass with dateline-crossing support

#### 11. Bridge Health Monitor (`gateway/bridge_health.py`)
- Tracks gateway bridge connection states, message rates, error classification
- `BridgeStatus` enum (HEALTHY/DEGRADED/OFFLINE) for header badge
- `DeliveryTracker` for LXMF message delivery latency metrics

#### 12. Unified Node Tracker (`gateway/node_tracker.py`)
- Authoritative node registry for both Meshtastic and RNS
- `to_geojson()` direct output - same format maps already consumes
- `_merge_node()` handles dual-network nodes (Meshtastic + RNS = "both")
- Writes to `/tmp/meshforge_rns_nodes.json` for cross-process access
- Signal quality history (snr_history, rssi_history) for sparklines

#### 13. Plugin Lifecycle (`core/plugin_base.py`)
- Maps already conforms to `PluginManifest` schema
- Could implement `Plugin.activate()`/`deactivate()` for tighter integration
- `PluginState` state machine (STARTING/RUNNING/DEGRADED/ERROR/STOPPED) for MapServer

#### 14. Map Data Service (`utils/map_data_service.py`)
- Core's map HTTP server with WebSocket integration
- `get_all_ips()` for multi-interface environments
- `--collect-only` debug mode, `--status` CLI flag
- Signal handling (SIGTERM/SIGINT) with `_stop_all_services()`

---

## Reliability Patterns Comparison

| Pattern | MeshForge Core | meshforge-maps Current | Gap |
|---------|---------------|----------------------|-----|
| Circuit breaker | Per-destination with registry | None | Critical gap |
| Retry with backoff | `ReconnectStrategy` + jitter | Basic MQTT retry only | Significant gap |
| Event-driven updates | `EventBus` pub/sub | Polling only | Significant gap |
| WebSocket push | `MessageWebSocketServer` | HTTP polling | Major UX gap |
| Health scoring | 0-100 composite with trends | Boolean `data_stale` | Feature gap |
| Thread safety | RLock + Lock everywhere | Lock in MQTT store only | Moderate gap |
| Port fallback | Pre-check + fallback range | 5-port fallback | Good alignment |
| Stale cache fallback | Connection manager + cache | BaseCollector + cache | Good alignment |
| Graceful degradation | DependencyStatus tracking | Optional imports OK | Good alignment |
| Node eviction | LRU + stale timeout | LRU + stale timeout | Good alignment |
| Coordinate validation | validate_coordinates | validate_coordinates | Good alignment |
| GeoJSON standard | RFC 7946 throughout | RFC 7946 throughout | Aligned |
| Subprocess safety | No shell=True, timeouts | No shell=True, timeouts | Aligned |
| Service worker tiles | N/A (server-side cache) | sw-tiles.js (client-side) | Complementary |

---

## Recommended Implementation Order

### Phase 1: Reliability Foundation (Next Session)
1. Add `CircuitBreakerRegistry` to `DataAggregator` (per-collector circuit breakers)
2. Add `ReconnectStrategy` to MQTT subscriber with jitter
3. Add retry with backoff to `BaseCollector.collect()` before cache fallback
4. Expose circuit breaker + collector states in `/api/status`

### Phase 2: Real-Time Architecture (Session After)
1. Add WebSocket server on adjacent port
2. Connect MQTT subscriber events to WebSocket broadcast
3. Update frontend to accept WebSocket push alongside polling
4. Add event bus for decoupled collector-to-server communication

### Phase 3: Feature Depth
1. Node history DB with `get_trajectory_geojson()`
2. Health scoring with `/api/health` endpoint
3. Topology visualization with SNR-based edge coloring
4. Shared health state reader for cross-process visibility

### Phase 4: Integration Polish
1. Connection manager integration for MeshtasticCollector
2. Server-side tile pre-caching capability
3. Plugin lifecycle hooks (`activate`/`deactivate`)
4. Signal handling and graceful shutdown improvements

---

## Files Analyzed from MeshForge Core

| File | Size | Key Patterns |
|------|------|-------------|
| `core/plugin_base.py` | 21KB | Plugin lifecycle, manifest, context injection, state machine |
| `gateway/circuit_breaker.py` | 13KB | Per-destination circuit breaker with registry, thread-safe |
| `gateway/reconnect.py` | 12KB | Exponential backoff, jitter, slow-start recovery |
| `gateway/bridge_health.py` | 23KB | Bridge health monitor, delivery tracker, error classification |
| `gateway/node_tracker.py` | 39KB | Unified node tracking, GeoJSON export, RNS integration |
| `utils/event_bus.py` | 9KB | Thread-safe pub/sub, typed events, safe callbacks |
| `utils/health_score.py` | 23KB | Composite 0-100 scoring, trend detection, per-node health |
| `utils/tile_cache.py` | 17KB | Region download, rate limiting, expiration management |
| `utils/connection_manager.py` | 16KB | Singleton TCP connection, lock, cache fallback |
| `utils/map_data_service.py` | 19KB | Map HTTP server, WebSocket, CLI, multi-interface |
| `utils/shared_health_state.py` | 24KB | SQLite cross-process health, WAL mode, latency percentiles |
| `utils/node_history.py` | 15KB | SQLite node observations, trajectory GeoJSON, snapshots |
| `utils/websocket_server.py` | 11KB | Async WebSocket broadcast, history buffer, stats |
| `utils/topology_visualizer.py` | 45KB | D3.js/GeoJSON/GraphML export, SNR coloring, node types |

---

## Architecture Notes

### Core MeshForge Architecture (v0.5.3)
```
src/
├── launcher_tui/          # 35 mixins, whiptail/dialog TUI
├── gateway/               # RNS-Meshtastic bridge, circuit breaker, node tracking
├── monitoring/            # MQTT subscriber, traffic inspector, packet dissectors
├── core/                  # Plugin system, orchestrator, meshtastic CLI
├── plugins/               # EAS alerts, meshcore, meshing-around, MQTT bridge
├── utils/                 # 80+ utility modules (RF, maps, health, diagnostics)
├── services/              # Service manager
└── commands/              # Propagation, hamclock
```

### Key Integration Points for meshforge-maps
1. **Cache files:** `~/.local/share/meshforge/mqtt_nodes.json`, `rns_nodes.json`, `node_cache.json`
2. **Shared health DB:** `~/.config/meshforge/health_state.db`
3. **Plugin manifest:** Already aligned with `PluginManifest` schema
4. **Event bus:** Global singleton for node/service events
5. **Port map:** Core uses 5000/5001/8081/9090; maps uses 8808+

---

## Session Entropy Watch

Session remained focused and productive throughout. No signs of entropy:
- Systematic task list maintained
- Each file analyzed methodically
- Clear priority tiers established
- Actionable implementation phases defined

**Session complete.** Ready for implementation in next session starting with Phase 1 (Reliability Foundation).

---

# Session 9 — Phase 4: Integration Polish

**Date:** 2026-02-08
**Version:** 0.4.0-beta → 0.5.0-beta
**Tests:** 348 → 424 passing (+76 new tests)

## What Was Built

### 1. Connection Manager (`src/utils/connection_manager.py`)
- Thread-safe singleton lock per meshtasticd host:port
- Prevents TCP contention between MeshForge core gateway and maps collector
- Context manager with configurable timeout (default 5s)
- Diagnostic stats: acquisitions, timeouts, held duration
- MeshtasticCollector now acquires exclusive access before API calls

### 2. Frontend Topology GeoJSON Rendering
- Replaced legacy `/api/topology` with `/api/topology/geojson` endpoint
- Frontend now uses native Leaflet `L.geoJSON()` layer instead of manual polylines
- Server-side SNR colors rendered directly (no duplicate client-side classification)
- Popups show network type and AREDN link type when available

### 3. Frontend Trajectory Visualization & Node History Panel
- New "Node History" toggle in Overlays section
- Floating history panel listing all tracked nodes with observation counts
- Click to show/hide trajectory polylines per node
- Start marker (green) and end marker (red) on each trajectory
- Auto-fits map bounds to trajectory when shown
- Trajectory rendered as dashed amber polyline with popup metadata

### 4. OpenHamClock API Compatibility Layer (`src/utils/openhamclock_compat.py`)
- `detect_variant()`: auto-detects HamClock vs OpenHamClock from get_sys.txt
- `normalize_spacewx()`: normalizes lowercase/alternate space weather keys
- `normalize_de_dx()`: normalizes DE/DX location keys (longitude, callsign aliases)
- `normalize_band_conditions()`: normalizes band key formats
- `get_endpoint_map()`: returns variant-specific endpoint paths
- HamClockCollector now uses endpoint map and key normalization throughout

### 5. Plugin Lifecycle State Machine (`src/utils/plugin_lifecycle.py`)
- Formal 6-state machine: LOADED → ACTIVATING → ACTIVE → DEACTIVATING → STOPPED → ERROR
- Validated transitions with `InvalidTransitionError` for illegal moves
- Context managers: `lifecycle.activating()` and `lifecycle.deactivating()`
- Transition listener callbacks for monitoring
- Uptime tracking and diagnostic `info` property
- MeshForgeMapsPlugin now uses lifecycle for activate/deactivate
- Status tool output includes state and uptime

## Test Coverage Added (76 new tests)

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_connection_manager.py` | 19 | Singleton, acquire/release, timeout, stats, thread safety |
| `test_plugin_lifecycle.py` | 31 | All state transitions, context managers, errors, listeners, info |
| `test_openhamclock_compat.py` | 26 | Variant detection, key normalization, endpoint maps |

## Files Changed

| File | Change |
|------|--------|
| `src/utils/connection_manager.py` | **NEW** — ConnectionManager singleton |
| `src/utils/openhamclock_compat.py` | **NEW** — OpenHamClock compatibility layer |
| `src/utils/plugin_lifecycle.py` | **NEW** — Plugin lifecycle state machine |
| `src/collectors/meshtastic_collector.py` | Uses ConnectionManager for API access |
| `src/collectors/hamclock_collector.py` | Uses compat layer for variant detection |
| `src/main.py` | Uses PluginLifecycle for activate/deactivate |
| `web/meshforge_maps.html` | Topology GeoJSON, trajectory UI, history panel |
| `manifest.json` | Version bump to 0.5.0-beta |
| `README.md` | Version and test count badge updates |
| `tests/test_connection_manager.py` | **NEW** — 19 tests |
| `tests/test_plugin_lifecycle.py` | **NEW** — 31 tests |
| `tests/test_openhamclock_compat.py` | **NEW** — 26 tests |
| `tests/test_collectors.py` | Fixed OpenHamClock fallback test for detect_variant |
