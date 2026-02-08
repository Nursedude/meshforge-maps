# Session Notes

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
