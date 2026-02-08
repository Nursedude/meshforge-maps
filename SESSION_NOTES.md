# Session Notes: MeshForge Core Feature & Reliability Analysis

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
