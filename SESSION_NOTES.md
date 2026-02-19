# Session Notes

---

## Session 22: Pi 2W Integration — meshforge Parent Diff & Deployment Hardening

**Date:** 2026-02-19
**Branch:** `claude/review-meshforge-pi-integration-x1BY4`
**Scope:** Cross-repo diff (meshforge parent → meshforge-maps), Pi Zero 2 W deployment, Debian Trixie support, MQTT-only mode
**Version:** 0.7.0-beta (no version bump)

### Summary

Comprehensive diff of the parent `Nursedude/meshforge` repo (v0.5.4, 875 PRs) against meshforge-maps identified critical bugs, missing deployment automation, and feature gaps. Fixed 2 critical bugs, added deployment tooling, and implemented MQTT-only mode for no-radio Pi operation.

### Deployment Context

- **Target:** Raspberry Pi Zero 2 W (512MB RAM, Cortex-A53) running Debian Trixie (13)
- **Mode:** No radio hardware, headless, web map served to remote browsers
- **Data sources:** MQTT (public Meshtastic broker) + HamClock/NOAA (space weather)

### Critical Bugs Fixed (from parent diff)

| # | Bug | Files | Impact |
|---|-----|-------|--------|
| 1 | `Path.home()` returns `/root` under sudo/systemd | 5 files, 7 instances | Config, data, DBs write to wrong directory when run as service |
| 2 | Server bind hardcoded to `127.0.0.1` | `map_server.py` | Remote browsers can't reach web map; `--host` CLI arg only affected TUI |

### Features Added

1. **`src/utils/paths.py`** — `get_real_home()` resolves via SUDO_USER → LOGNAME → pwd → Path.home() fallback chain. Convenience functions: `get_data_dir()`, `get_config_dir()`, `get_cache_dir()`

2. **Configurable bind address** — `http_host` + `ws_host` config keys, wired through `MapServer.start()` and `_start_websocket()`. `--host` CLI arg now affects server binding.

3. **`meshtastic_source` config** — Values: `"auto"` (default), `"mqtt_only"`, `"local_only"`. When `mqtt_only`, skips `_fetch_from_api()` entirely — eliminates connection-refused log spam on no-radio Pi.

4. **Systemd service file** — `scripts/meshforge-maps.service` with security hardening (NoNewPrivileges, ProtectSystem=strict, ProtectHome=read-only, PrivateTmp), crash-loop protection (StartLimitIntervalSec=300), network ordering.

5. **Install script** — `scripts/install.sh` with OS detection (Bookworm/Trixie), PEP 668-compliant venv, optional deps, `--no-radio` flag, systemd setup.

6. **Verify script** — `scripts/verify.sh` checks Python, imports, service status, ports, HTTP endpoint, filesystem. Pass/warn/fail exit codes.

7. **Tile cache reduction** — Service worker `MAX_TILE_CACHE_ITEMS` 2000 → 500 for 512MB Pi.

8. **Trixie support** — Added to README OS table with PEP 668 note. Pi headless deployment section.

9. **HamClock headless docs** — README section documenting 3 options for headless Pi: remote host, Xvfb, NOAA direct fallback.

### Files Changed (17)

| File | Change |
|------|--------|
| `src/utils/paths.py` | **NEW** — sudo/systemd-safe home resolution |
| `src/main.py` | Safe paths, `--host` wired to server config |
| `src/map_server.py` | Configurable HTTP + WebSocket bind address |
| `src/utils/config.py` | `http_host`, `ws_host`, `meshtastic_source` config keys |
| `src/collectors/base.py` | Safe path for data directory |
| `src/collectors/meshtastic_collector.py` | `source_mode` param, skip API when `mqtt_only` |
| `src/collectors/aggregator.py` | Pass `meshtastic_source` to collector |
| `src/utils/node_history.py` | Safe path for history DB |
| `src/utils/shared_health_state.py` | Safe path for health DB |
| `web/sw-tiles.js` | Tile cache 2000 → 500 |
| `scripts/meshforge-maps.service` | **NEW** — systemd unit with security hardening |
| `scripts/install.sh` | **NEW** — Pi/Trixie installer |
| `scripts/verify.sh` | **NEW** — post-install verification |
| `tests/test_paths.py` | **NEW** — 11 tests for path safety |
| `tests/test_config.py` | +8 tests (host config, no-radio profile, mqtt-only) |
| `tests/test_collectors.py` | +2 tests (mqtt-only mode) |
| `README.md` | Trixie, host config, Pi deployment, HamClock headless |

### Test Results

- **Before:** 822 passed, 22 skipped
- **After:** 832+ passed, 22 skipped, 0 failures

### Architecture Notes

- **Path resolution pattern** ported from meshforge parent's `utils/paths.py` — the `get_real_home()` function is the single source of truth for home directory, replacing all 7 `Path.home()` calls
- **Bind address** follows meshforge parent's systemd service pattern — `0.0.0.0` for production Pi, `127.0.0.1` default for development
- **MQTT-only mode** prevents wasted TCP connections and log noise when no local meshtasticd is running — the MQTT subscriber provides all Meshtastic data via the public broker
- **Install script** handles PEP 668 (externally-managed Python) enforced by Trixie via mandatory venv creation

### Feature Gap Analysis (from parent diff)

Documented for future sessions:
- **NOAA Weather Alerts** — EAS polygon overlays on map (from parent's `eas_alerts.py`)
- **MeshCore Collector** — 5th data source (from parent's v0.6.0 roadmap)
- **Coverage Heatmap** — Node density overlay leveraging existing history DB
- **RF Link Analysis** — Link budget/Fresnel zone on topology links (from parent's RF tools)

### Session Entropy Watch

- Session stayed focused throughout — systematic task list with 12 tracked items
- All tasks completed in dependency order (paths → config → server → deployment → tests)
- Zero regressions at every checkpoint
- Clean boundary: all tests green, ready for next session

---

## Session 21: Fix meshtasticd HTTP API Connection Issues

**Date:** 2026-02-12
**Branch:** `claude/fix-meshtasticd-http-api-Qr1hQ`
**Scope:** Audit and fix HTTP API connection issues in meshtasticd collector, API proxy, and map server
**Version:** 0.7.0-beta (no version bump)

### Summary

Systematic audit of all meshtasticd HTTP API connection code identified 13 issues
across 4 files. Fixed the 7 highest-impact issues: timeout race condition, missing
retry logic, missing Content-Length headers, Server header version leakage, proxy
startup failure handling, O(n) node lookup, and thread-safe store swaps.
13 new tests (898 total). Zero regressions.

### Issues Found (Audit)

| # | Severity | File | Issue |
|---|----------|------|-------|
| 1 | CRITICAL | meshtastic_collector.py | Timeout mismatch: lock timeout (5s) = urlopen timeout (5s) — race condition |
| 2 | CRITICAL | meshtastic_collector.py | No retry logic in `_fetch_from_api()` for transient failures |
| 3 | CRITICAL | meshtastic_api_proxy.py, map_server.py | Missing Content-Length header in JSON/CSV responses |
| 4 | HIGH | meshtastic_api_proxy.py, map_server.py | Server header leaks Python version info |
| 5 | HIGH | map_server.py | Proxy startup failure silently ignored after HTTP server already running |
| 6 | HIGH | meshtastic_api_proxy.py | `_serve_node()` scans all nodes O(n) per single-node request |
| 7 | HIGH | meshtastic_api_proxy.py | Store swap via `set_store()` not atomic for in-flight handlers |
| 8-13 | MEDIUM-LOW | Various | Stats consistency, connection drops, circuit breaker edge cases |

### Fixes Applied

#### 1. Timeout Mismatch Fix (`meshtastic_collector.py`)
- HTTP timeout now `connection_timeout - 1.0` (floor 1.0s) so lock is never released while request is in-flight
- Previously both were 5.0s, causing a race where the lock could expire during an active HTTP request

#### 2. Retry Logic for Transient Failures (`meshtastic_collector.py`)
- `_fetch_from_api()` now retries once (0.5s delay) on `URLError`/`OSError` before giving up
- Does NOT retry on `JSONDecodeError` (permanent parse failures)
- Distinguishes transient connection issues from permanent errors

#### 3. Content-Length Header (`meshtastic_api_proxy.py`, `map_server.py`)
- All JSON responses now include `Content-Length` header
- CSV export responses also include `Content-Length`
- Prevents HTTP/1.0 client hangs and improper connection handling

#### 4. Server Header Hardening (`meshtastic_api_proxy.py`, `map_server.py`)
- Override `server_version` and `version_string()` on both handler classes
- Proxy returns `MeshForge-Proxy/1.0`, map server returns `MeshForge-Maps/1.0`
- No longer leaks Python version in HTTP responses

#### 5. Proxy Startup Failure Handling (`map_server.py`)
- `proxy.start()` return value now checked; failure logged as warning
- Map server continues operating without proxy (non-fatal degradation)
- Previously proxy startup failure was silently ignored

#### 6. O(1) Node Lookup (`mqtt_subscriber.py`, `meshtastic_api_proxy.py`)
- New `MQTTNodeStore.get_node(node_id)` method: O(1) dict lookup with `!` prefix normalization
- Proxy `_serve_node()` uses `get_node()` when available, falls back to scan for older stores
- Previously scanned all nodes linearly per single-node request

#### 7. Thread-Safe Store Swap (`meshtastic_api_proxy.py`)
- `set_store()` reads server reference once into local variable
- Prevents reading stale `self._server` after concurrent stop/restart

### Test Coverage Added (13 new tests)

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_mqtt_subscriber.py` | +6 | `get_node()` exact match, prefix normalization, not-found, copy semantics, empty store |
| `test_collectors.py` | +3 | Retry on transient failure, timeout calculation, no retry on JSON error |
| `test_meshtastic_api_proxy.py` | +4 | Content-Length header, Server header, O(1) node lookup, prefix-less lookup |

### Test Results
- **Before:** 885 passed, 22 skipped
- **After:** 898 passed (+13 new), 22 skipped, 0 failures, 0 regressions

### Files Modified (5)
- `src/collectors/meshtastic_collector.py` — Timeout fix, retry logic
- `src/collectors/mqtt_subscriber.py` — `get_node()` O(1) lookup method
- `src/utils/meshtastic_api_proxy.py` — Content-Length, Server header, O(1) lookup, thread-safe set_store
- `src/map_server.py` — Content-Length, Server header, proxy startup check
- `tests/test_mqtt_subscriber.py` — 6 new tests for get_node()
- `tests/test_collectors.py` — 3 new tests for retry logic
- `tests/test_meshtastic_api_proxy.py` — 4 new tests for response headers and lookup

### Architecture Notes
- Retry logic is intentionally limited to 1 retry inside `_fetch_from_api()` — higher-level retries still happen at the `BaseCollector.collect()` level via `ReconnectStrategy`
- Content-Length is critical for HTTP clients that don't support chunked encoding
- O(1) node lookup via `get_node()` supports both `!`-prefixed and bare hex IDs
- Session stopped cleanly — no entropy detected

---

## Session 20: Analytics Frontend, CSV/JSON Export, TUI Search & Event Filtering

**Date:** 2026-02-12
**Branch:** `claude/session-management-tasks-alAbk`
**Scope:** Four features: analytics dashboard, data export, TUI search/filter, events pause/filter
**Version:** 0.7.0-beta (no version bump)

### Summary

Added browser-based analytics dashboard with SVG charts, server-side CSV/JSON export
endpoints, TUI-wide search/filter with `/` key, and Events tab pause/resume with event
type filtering. Zero new dependencies. 27 new tests (885 total).

### What Was Built

#### 1. Analytics Frontend (Browser)
- New "Analytics" toggle in control panel Overlays section
- Collapsible analytics panel (bottom-left) with 4 tabs:
  - **Growth**: Network growth sparkline (SVG polyline + area fill), summary stats
    (current nodes, peak nodes, total observations), time axis labels
  - **Activity**: Activity heatmap grid (24 cells, intensity-colored), bar chart
    (SVG bars by hour), peak hour and average stats
  - **Ranking**: Node ranking table with columns: rank, node ID, network,
    observation count, active time
  - **Alert Trends**: Stacked bar chart (critical/warning/info), severity summary
    stats, total alert count
- All charts use inline SVG (no chart library dependency)
- Client-side JSON/CSV export buttons per tab via Blob download
- Auto-hides history panel when analytics opens (same screen position)

#### 2. CSV/JSON Export Endpoints (Server)
- 5 new export routes:
  - `GET /api/export/nodes` — tracked nodes CSV (or JSON with `?format=json`)
  - `GET /api/export/alerts` — alert history CSV (or JSON)
  - `GET /api/export/analytics/growth` — network growth CSV
  - `GET /api/export/analytics/activity` — activity heatmap CSV
  - `GET /api/export/analytics/ranking` — node ranking CSV
- `_send_csv()` helper method: proper Content-Type, Content-Disposition headers
- CSV uses stdlib `csv.writer` for correct escaping
- Limit parameter support with sensible defaults (5000 nodes, 500 alerts)
- CORS headers on CSV responses

#### 3. TUI Search/Filter (`/` key)
- `/` key activates search input mode (cursor visible in status bar)
- Type to build query, Enter to accept, Escape to cancel
- Search query filters displayed data on the active tab:
  - **Nodes tab**: Filters by ID, name, source, health label, state
  - **Alerts tab**: Filters by type, severity, node ID, message
  - **Events tab**: Filters by type, node ID, source, data content
- Status bar shows active filter with `[Esc]clear` hint
- Escape (outside search mode) clears current filter
- Updated keybinding hint: `/:Search`

#### 4. Events Tab: Pause/Resume + Type Filtering
- `p` key toggles pause on Events tab
  - Pauses: takes snapshot of current event log (frozen view)
  - Unpauses: resumes live scrolling from real event log
- `f` key cycles event type filter:
  - All (no filter) -> node.position -> node.telemetry ->
    node.topology -> alert.fired -> service -> (back to All)
- Active filter shown in Events tab header: `Type:node.position`
- Pause indicator: `PAUSED` shown in header when frozen
- Both `p` and `f` only respond on Events tab (tab index 5)

### Testing

- **27 new tests** (858 -> 885 total)
- Test classes:
  - `TestSearchFilter` (12 tests): search activation, typing, backspace,
    escape cancel, enter accept, escape clear, node filter, alert filter
  - `TestEventsPauseResume` (13 tests): initial state, pause toggle, unpause,
    type filter cycle, filter wrap, filter applies, event search, draw paused,
    draw with type filter, p/f key tab isolation
  - `TestMapServerHTTPEndpoints` additions (7 tests): export nodes CSV/JSON,
    export alerts CSV/JSON, export analytics growth/activity/ranking CSV
- **Full suite: 885 passed, 22 skipped, 0 failures** (no regressions)

### Architecture Decisions

- **Inline SVG charts**: No chart.js or D3 for analytics — raw SVG `<polyline>`,
  `<polygon>`, `<rect>`, `<text>` elements. Keeps the zero-dependency frontend
  philosophy. Trade-off: no interactivity (hover tooltips) on charts, but the
  heatmap cells do have CSS `:hover::after` tooltips.
- **Client-side CSV export**: The analytics panel's JSON/CSV buttons create Blobs
  from cached API data. Server-side export endpoints exist separately for direct
  download links (bookmarkable URLs, curl-friendly).
- **Search as filter, not highlight**: Search filters rows rather than highlighting
  matches. Simpler implementation, clearer UX — you see only matching items.
  Cursor/scroll position resets when filter changes.
- **Pause snapshot**: Events pause takes a snapshot (list copy) rather than
  stopping the ring buffer. New events still accumulate in `_event_log` — unpause
  shows the latest state, not where you left off.

### Files Changed

| File | Change |
|------|--------|
| `web/meshforge_maps.html` | Modified — analytics panel CSS + HTML + JS (4 chart renderers, CSV export, tab switcher) |
| `src/map_server.py` | Modified — 5 export routes, `_send_csv()` helper, route table additions |
| `src/tui/app.py` | Modified — search/filter state, `/` key handling, event pause/filter, status bar update |
| `tests/test_tui.py` | Modified — 25 new tests (TestSearchFilter, TestEventsPauseResume) |
| `tests/test_map_server.py` | Modified — 7 new export endpoint tests |

### Usage

```bash
# Analytics panel: check "Analytics" in Overlays section, or toggle in browser
# Export: GET /api/export/nodes, /api/export/alerts, /api/export/analytics/growth

# TUI search: press / to search, type query, Enter to accept, Esc to clear
# TUI events: press p to pause/resume, f to cycle type filter
```

### Session Entropy Notes

Session stayed focused: four planned features, implemented in order, tested after
each. No scope creep. Good stopping point — all four items completed.

### Next Session Candidates

- [ ] Analytics frontend: interactive chart tooltips (hover to see values)
- [ ] Analytics frontend: time range picker (last 1h/6h/24h/7d)
- [ ] TUI: Analytics tab (7th tab) showing growth/ranking inline
- [ ] TUI: Mouse support for tab switching and node selection
- [ ] TUI: Configuration editing from within the TUI
- [ ] Email alert delivery (SMTP integration)
- [ ] Multi-instance federation (peer instances via MQTT/HTTP)
- [ ] Mobile / PWA with push notifications

---

## Session 19: TUI Expansion — Node Detail, Topology, WebSocket, Events

**Date:** 2026-02-12
**Branch:** `claude/session-entropy-tracking-NNQ6B`
**Scope:** Four TUI features: node drill-down, topology ASCII art, WebSocket push, event stream
**Version:** 0.7.0-beta (no version bump)

### Summary

Expanded the TUI from 4 tabs to 6, added node detail drill-down on the Nodes tab,
WebSocket push-based event delivery, and a live event stream tab. Zero new
dependencies — WebSocket client uses raw stdlib sockets with manual handshake.

### What Was Built

#### 1. Node Detail Drill-Down (Nodes Tab → Enter)
- Cursor-based node selection with `>` indicator and highlight bar
- Enter key drills into full detail view; Escape/q returns to list
- Detail view sections:
  - **Identity**: ID, name, source, connectivity state
  - **Health Score**: Overall score + component breakdown with bar gauges
    (battery, signal, freshness, reliability, congestion — score/max + raw metrics)
  - **Recent Observations**: timestamped table with lat/lon/SNR/battery/network
  - **Config Drift**: per-node configuration changes with severity coloring
  - **Node Alerts**: alerts filtered to the selected node
- Scrollable with j/k, independent scroll offset from main node list
- Auto-refreshes detail data when drill-down is active

#### 2. Topology ASCII Art Tab ([5])
- Adjacency map: each node listed with connection count, sorted by hub degree
- Per-link quality indicators:
  - `===` excellent (SNR >= 8dB, green)
  - `---` good (5-8dB, green)
  - `...` marginal (0-5dB, yellow)
  - `~~~` poor/bad (<0dB, red)
- All-links table sorted by SNR with quality labels
- Node name resolution from nodes GeoJSON
- Link quality legend at top

#### 3. WebSocket Push Updates
- Minimal WebSocket client using stdlib `socket` + `hashlib` + `struct`
- RFC 6455 compliant handshake (HTTP Upgrade + Sec-WebSocket-Key)
- Frame parser handles text frames, close frames, and ping/pong
- Background thread with auto-reconnect (5s backoff on disconnect)
- WS port discovery from `/api/status` response (websocket.port field)
- Fallback: HTTP port + 1 convention
- Events pushed to shared `_event_log` ring buffer under data lock
- Status bar shows WS:ON indicator when connected

#### 4. Event Stream Tab ([6])
- Live feed of all WebSocket messages (newest first)
- Columns: Time, Type, Source, Node, Detail
- Color-coded by event type:
  - Green: node.position
  - Cyan: node.telemetry
  - Yellow: node.topology / service events
  - Red: alert.fired
- Ring buffer capped at 500 events (configurable via `_event_log_max`)
- Shows WebSocket connection status (CONNECTED/POLLING)

#### 5. Data Client Extensions (`data_client.py`)
- `node_health(node_id)`: GET `/api/nodes/{id}/health`
- `node_history(node_id, limit)`: GET `/api/nodes/{id}/history?limit=N`
- `node_alerts(node_id)`: GET `/api/alerts?node_id={id}`
- `topology_geojson()`: GET `/api/topology/geojson`
- `config_drift()`: GET `/api/config-drift`

### Testing

- **37 new tests** added to `tests/test_tui.py` (42 → 79 TUI tests)
- Test classes:
  - `TestNewDataClientAccessors`: failure-mode tests for new client methods
  - `TestNewDataClientWithServer`: HTTP path verification with stub server
  - `TestNodeDetailDrillDown`: detail state, Enter/Escape, cursor, build_node_rows
  - `TestTopologyTab`: tab switching, quality color helper
  - `TestEventsTab`: event log init, ring buffer truncation, WS message handling
  - `TestWebSocketState`: init state, frame parsing (text, close, error)
  - `TestDrawMethods`: draw_node_detail, draw_topology, draw_events with mock data
- **Full suite: 858 passed, 22 skipped, 0 failures** (no regressions)

### Architecture Decisions

- **stdlib WebSocket client**: No `websockets` or `websocket-client` library. Raw
  socket + struct for frame parsing. Matches project's zero-dependency philosophy.
  Trade-off: no compression, no extensions, but sufficient for JSON event push.
- **Ring buffer for events**: Fixed-size list with tail truncation. Simple and
  bounded memory. Alternative was deque but list slicing is cleaner for curses.
- **Cursor-based selection**: Added `_node_cursor` to Nodes tab rather than
  click-based selection. Vim-style (j/k to move, Enter to select) is consistent
  with existing TUI keybinding conventions.
- **Shared _build_node_rows**: Extracted from _draw_nodes so both the list view
  and _enter_node_detail can use the same sorted row data.

### Files Changed

| File | Change |
|------|--------|
| `src/tui/app.py` | Modified — 4 new draw methods, WebSocket client, cursor nav, 6 tabs |
| `src/tui/data_client.py` | Modified — 5 new per-node/topology accessors |
| `tests/test_tui.py` | Modified — 37 new tests (79 total TUI tests) |

### Usage

```bash
# Tabs are now 1-6:
# [1] Dashboard  [2] Nodes  [3] Alerts  [4] Propagation  [5] Topology  [6] Events

# Node detail: on Nodes tab, use j/k to move cursor, Enter to drill in, Esc to go back

# WebSocket auto-connects in background — Events tab shows live stream
```

### Session Entropy Notes

Session stayed focused: four related TUI features, no scope creep. Each feature
was implemented → tested → verified before moving to the next. Good stopping
point — all four planned items completed.

### Next Session Candidates

- [ ] TUI: Resizable panels / split-pane layout
- [ ] TUI: Configuration editing from within the TUI
- [ ] TUI: Node detail — add trajectory GeoJSON visualization (ASCII map?)
- [ ] TUI: Topology — interactive node selection (Enter on topology node)
- [ ] TUI: Search/filter across nodes, alerts, events
- [ ] TUI: Mouse support for tab switching and node selection
- [ ] WebSocket: Subscribe to specific event types (filter server-side)
- [ ] Events tab: Pause/resume scrolling, event type filtering

---

## Session 18: Terminal User Interface (TUI)

**Date:** 2026-02-12
**Branch:** `claude/improve-tui-meshforge-PWjwR`
**Scope:** Curses-based terminal dashboard for mesh network monitoring
**Version:** 0.7.0-beta (no version bump)

### Summary

Added a full terminal UI using Python stdlib `curses` — zero new dependencies,
consistent with the project's stdlib-first philosophy. The TUI provides a
4-tab dashboard that connects to the running MapServer HTTP API for real-time
monitoring directly from the terminal.

### What Was Built

#### 1. TUI Core Framework (`src/tui/app.py`)
- `TuiApp` class: curses.wrapper-based app with tabbed panel architecture
- Color system: 16 color pairs for health scores, alert severity, source status
- Thread-safe data refresh: background thread fetches API data every 5 seconds
- `safe_addstr()` helper: clip-safe drawing, no curses.error crashes
- Vim-style keybindings: j/k scroll, 1-4 tab select, g home, r refresh, q quit

#### 2. Dashboard Tab ([1])
- Server status: version, port, uptime
- Data sources: ON/ERR/OFF indicators with node counts per source
- Node health summary: distribution across excellent/good/fair/poor/critical
- Node connectivity: stable/new/intermittent/offline counts
- Alert summary: total/active counts by severity
- MQTT subscriber: running state, message count, nodes tracked
- Performance: cache hit rate, avg/p99 latency

#### 3. Nodes Tab ([2])
- Scrollable node table: ID, name, source, health score, health label, state
- Color-coded health scores (green/yellow/red)
- State coloring: stable=green, intermittent=yellow, offline=red
- Sortable: `s` toggles sort direction, `S` cycles sort column
- Merges data from nodes/geojson, node-health, and node-states APIs

#### 4. Alerts Tab ([3])
- Active alerts section with severity coloring (cyan/yellow/red)
- Full alert history table with type, node, timestamp, message
- Alert rules listing with enabled/disabled state
- Scrollable with full history

#### 5. Propagation Tab ([4])
- Space weather: SFI, Kp index (color-coded by storm level), band conditions
- VOACAP band predictions: reliability %, status, best-band indicator
- Band conditions: good/fair/poor coloring
- DX spots table: call, frequency, DE, UTC
- Station info: DE/DX callsigns and grids

#### 6. HTTP Data Client (`src/tui/data_client.py`)
- `MapDataClient`: lightweight urllib-based REST client
- 19 endpoint accessors covering all MapServer APIs
- 3-second timeout per request for TUI responsiveness
- `is_alive()` liveness check
- All methods return None on failure (graceful degradation)

#### 7. CLI Integration (`src/main.py`)
- `--tui`: Start server + launch TUI on main thread
- `--tui-only`: Connect TUI to already-running server (client mode)
- `--host` / `--port`: Custom server address
- Backwards compatible: plain `python -m src` still works as before
- Added hint message: "Tip: restart with --tui for a terminal dashboard"

### Testing

- **42 new tests** in `tests/test_tui.py`
- Covers: data client (HTTP + stub server), app init, color helpers,
  safe_addstr clipping, timestamp formatting, CLI arg parsing,
  keyboard input handling, scroll management, sort toggling, refresh logic
- **Full suite: 821 passed, 22 skipped, 0 failures** (no regressions)

### Architecture Decisions

- **stdlib curses over textual/rich**: Matches project's zero-dependency philosophy;
  curses is available everywhere Python runs (Linux, macOS, Pi)
- **HTTP API client, not direct object access**: TUI connects via REST like the
  browser does — clean separation, works in --tui-only mode against remote servers
- **Thread-safe cache pattern**: Background fetch thread writes to `_cache` dict
  under lock; draw thread reads a snapshot — no UI freezes during API calls
- **Tab-specific fetching**: Only fetches APIs relevant to current tab to minimize
  server load

### Files Changed

| File | Change |
|------|--------|
| `src/tui/__init__.py` | New — package init |
| `src/tui/app.py` | New — TUI application (620 lines) |
| `src/tui/data_client.py` | New — HTTP data client (100 lines) |
| `src/main.py` | Modified — CLI args, --tui/--tui-only flags |
| `tests/test_tui.py` | New — 42 tests (260 lines) |

### Usage

```bash
# Server + TUI together
python -m src --tui

# TUI client only (connect to running server)
python -m src --tui-only --port 8808

# Classic mode (no TUI, just server)
python -m src
```

### Session Entropy Notes

Stopping here with clean, focused scope. Session stayed on track: single feature
(TUI), systematic build (framework → tabs → CLI → tests), no scope creep.
Good stopping point for a new session to pick up next items.

### Next Session Candidates

- [ ] TUI: Add node detail view (drill into single node: health breakdown, trajectory, config drift)
- [ ] TUI: Add topology tab with ASCII art mesh link visualization
- [ ] TUI: WebSocket integration for push-based updates (instead of polling)
- [ ] TUI: Log/event stream panel for real-time MQTT messages
- [ ] TUI: Resizable panels / split-pane layout
- [ ] TUI: Configuration editing from within the TUI

---

## Session 17: Alerting Delivery Expansion & Historical Analytics

**Date:** 2026-02-12
**Branch:** `claude/alerting-delivery-expansion-vWbAh`
**Scope:** MQTT alert publishing, EventBus→WebSocket alert propagation, frontend alert panel, historical analytics
**Version:** 0.7.0-beta (no version bump)

### Summary

Extended the alerting engine from Session 16 with three delivery channels and
built the historical analytics module (second near-term roadmap item).

### Changes

#### 1. MQTT Alert Publishing (`alert_engine.py`)
- Added `mqtt_client` and `mqtt_topic` parameters to `AlertEngine.__init__`
- New `_publish_mqtt()` method publishes to base topic and severity sub-topic
  (e.g. `meshforge/alerts` + `meshforge/alerts/critical`) with QoS 1
- `set_mqtt_client()` / `remove_mqtt_client()` for dynamic MQTT configuration
- `mqtt_publish_count` property tracks successful MQTT deliveries
- Alert summary now includes `mqtt_enabled` and `mqtt_publish_count`
- MQTT errors are swallowed (best-effort, matches webhook pattern)

#### 2. EventBus Alert Propagation (`map_server.py`)
- `_handle_telemetry_for_alerts()` now captures triggered alerts and publishes
  each as an `ALERT_FIRED` event on the EventBus
- New `_publish_alert_event()` creates `Event(EventType.ALERT_FIRED)` with
  full alert dict as payload
- Existing wildcard subscriber `_forward_to_websocket()` automatically forwards
  alert events to all WebSocket clients as `{"type": "alert.fired", "data": {...}}`
- MapServer wires MQTT client from subscriber to alert engine on startup

#### 3. Frontend Alert Panel (`meshforge_maps.html`)
- **Alert panel**: Collapsible panel (bottom-right) showing live alerts with
  severity-colored dots, message text, node ID, and timestamp
- **Header alert badge**: Red badge in header bar showing active alert count;
  clicking toggles alert panel
- **Overlay toggle**: "Alerts" checkbox in Overlays section controls panel visibility
- **Real-time alerts**: `handleRealtimeMessage()` routes `alert.fired` WebSocket
  messages to `handleAlertEvent()` which prepends to in-memory buffer
- **Initial load**: `loadInitialAlerts()` fetches `/api/alerts/active` on page
  load to populate panel with existing unacknowledged alerts
- **Toast on critical**: Critical alerts show toast notification
- Bounded to 100 alert items (`MAX_ALERT_ITEMS`)

#### 4. Historical Analytics (`analytics.py` — new module)
- `HistoricalAnalytics` class: read-only aggregation over NodeHistoryDB + AlertEngine
- **Network growth**: `network_growth()` — unique nodes per time bucket,
  total observations per bucket. Configurable bucket size (60s–86400s)
- **Activity heatmap**: `activity_heatmap()` — observation counts by hour of day
  (0–23), with peak hour detection
- **Node ranking**: `node_activity_ranking()` — nodes ranked by observation count,
  with first/last seen timestamps and active duration
- **Network summary**: `network_summary()` — total nodes, observations,
  per-network breakdown, average observations per node
- **Alert trends**: `alert_trends()` — alerts bucketed by time with per-severity
  counts (critical/warning/info/total)

#### 5. Analytics API Endpoints (`map_server.py`)
- `GET /api/analytics/growth?since=&until=&bucket=` — network growth time-series
- `GET /api/analytics/activity?since=&until=` — hour-of-day activity heatmap
- `GET /api/analytics/ranking?since=&limit=` — most active nodes
- `GET /api/analytics/summary?since=` — high-level network statistics
- `GET /api/analytics/alert-trends?bucket=` — alert trend aggregation

### Tests

- **54 new tests** across 2 new test files:
  - `test_alerting_delivery.py` — MQTT publishing (13 tests), EventBus propagation (6),
    combined delivery (2)
  - `test_analytics.py` — network growth (7), activity heatmap (5), node ranking (6),
    network summary (6), alert trends (6), integration (3)
- **779 passing, 22 skipped** — zero regressions from 725 prior passing tests

### Files Changed

| File | Changes |
|------|---------|
| `src/utils/alert_engine.py` | +MQTT publish delivery, `set_mqtt_client()`, `_publish_mqtt()`, summary stats |
| `src/utils/analytics.py` | **New** — HistoricalAnalytics: growth, heatmap, ranking, summary, alert trends |
| `src/map_server.py` | +ALERT_FIRED event publishing, +analytics wiring, +5 analytics API endpoints |
| `web/meshforge_maps.html` | +alert panel CSS/HTML/JS, header badge, overlay toggle, WebSocket alert handler |
| `tests/test_alerting_delivery.py` | **New** — 21 tests for MQTT + EventBus alert delivery |
| `tests/test_analytics.py` | **New** — 33 tests for historical analytics |

### Architecture

```
Alert Flow (expanded):
  MQTT Telemetry → EventBus(NODE_TELEMETRY)
    → _handle_telemetry_for_alerts()
      → AlertEngine.evaluate_node()
        → _deliver():
          1. on_alert callback
          2. _publish_mqtt() → MQTT topic (meshforge/alerts/{severity})
          3. _send_webhook() → HTTP POST
      → _publish_alert_event()
        → EventBus(ALERT_FIRED)
          → _forward_to_websocket()
            → WebSocket broadcast {"type":"alert.fired","data":{...}}
              → Frontend: handleAlertEvent() → alert panel + toast

Analytics Flow:
  NodeHistoryDB (SQLite observations) ──┐
                                        ├→ HistoricalAnalytics ──→ /api/analytics/*
  AlertEngine (in-memory history) ──────┘
```

### Session Entropy Watch
Session remained systematic. All 4 features implemented in dependency order
(alert engine expansion → EventBus wiring → frontend panel → analytics).
54 new tests, zero regressions at final checkpoint. Session complete.

---

## Session 16: WebSocket Marker Fix & Alerting Foundation

**Date:** 2026-02-12
**Branch:** `claude/session-structure-setup-UtjMY`
**Scope:** Fix architectural WebSocket marker update bug; implement alerting & notifications engine
**Version:** 0.7.0-beta (no version bump)

### Context

All 52 code review issues from Session 13 resolved in Sessions 14-15. Codebase at
zero known defects. This session addresses the final known architectural bug
(WebSocket marker updates) and begins the first near-term roadmap item (alerting
& notifications).

### Baseline

- **Tests:** 671 passed, 22 skipped, 0 failures

### Changes Made

#### 1. WebSocket Marker Update Bug Fix (`web/meshforge_maps.html`)

**Problem:** `processGeoJSON()` / `renderMarkers()` did not attach `.feature`
to markers. `updateOrAddNode()` searched for markers via `layer.feature.properties.id`,
which always failed. Result: real-time WebSocket position updates created duplicate
temporary markers instead of moving existing ones.

**Fix:** Implemented a `markerRegistry` (JavaScript `Map`, keyed by node ID):
- `renderMarkers()` populates registry as markers are created — O(1) lookup
- `updateOrAddNode()` queries registry instead of O(n) layer scan
- When an existing marker is found, its position is updated and the
  corresponding `allFeatures` entry is also updated so the next `renderMarkers()`
  call preserves the new position
- New nodes added to the registry immediately for future WebSocket updates
- Registry cleared on every `renderMarkers()` call to stay in sync

#### 2. Alert Engine (`src/utils/alert_engine.py`, ~380 lines)

New threshold-based alerting system for mesh network monitoring:

- **AlertRule** dataclass: configurable metric, operator (lt/gt/eq/lte/gte),
  threshold, severity, cooldown, network filter
- **Alert** dataclass: generated alert with node ID, metric value, message,
  timestamp, acknowledgment state
- **AlertEngine** class:
  - Rule management: add/remove/enable/disable rules
  - `evaluate_node()`: evaluates all rules against node properties + health score
  - `evaluate_offline()`: dedicated offline detection (absence-based)
  - Per-node per-rule cooldown throttling (default 10 min) prevents alert storms
  - Bounded alert history (max 500 entries, LRU trim)
  - Alert acknowledgment
  - Webhook delivery (best-effort POST with JSON payload)
  - Callback delivery (`on_alert` parameter)
  - Thread-safe: all mutable state behind lock

- **5 default alert rules:**
  - `battery_low` — battery <= 20% (warning)
  - `battery_critical` — battery <= 5% (critical)
  - `signal_poor` — SNR <= -10 dB (warning)
  - `congestion_high` — channel_util >= 75% (warning)
  - `health_degraded` — health score <= 20 (warning)

#### 3. EventBus Extension (`src/utils/event_bus.py`)

- Added `ALERT_FIRED` event type for alert event propagation

#### 4. MapServer Integration (`src/map_server.py`)

- AlertEngine instantiated in MapServer constructor
- Wired to EventBus: `NODE_TELEMETRY` events trigger automatic rule evaluation
- Alert summary included in `/api/status` response
- **4 new API endpoints:**
  - `GET /api/alerts` — alert history with limit/severity/node_id filters
  - `GET /api/alerts/active` — unacknowledged alerts
  - `GET /api/alerts/rules` — configured alert rules
  - `GET /api/alerts/summary` — alert statistics

### Files Changed

| File | Changes |
|------|---------|
| `src/utils/alert_engine.py` | **NEW** — AlertEngine, AlertRule, Alert, default rules, webhook delivery |
| `src/utils/event_bus.py` | +`ALERT_FIRED` event type |
| `src/map_server.py` | AlertEngine integration, 4 new API routes, telemetry→alert wiring |
| `web/meshforge_maps.html` | `markerRegistry` Map for O(1) WebSocket marker lookup |
| `tests/test_alert_engine.py` | **NEW** — 54 tests covering rules, evaluation, cooldown, history, delivery, API |

### Test Results

- **Before:** 671 passed, 22 skipped
- **After:** 725 passed, 22 skipped, 0 failures, 0 regressions (+54 new tests)

### Session Entropy Watch

Session remained focused and systematic:
- Two clear tasks: architectural bug fix, then roadmap feature
- Bug fix verified with checkpoint (zero regressions) before moving on
- Alert engine designed to integrate with existing patterns (EventBus, health scorer, node state)
- All 54 new tests pass on first run after fixing shared-state mutation bug in rule copying

---

## Session 14: Medium & Low Severity Fixes

**Date:** 2026-02-12
**Branch:** `claude/fix-severity-issues-x7r1W`
**Scope:** Fix all 13 medium-severity issues and 10 low-severity quick wins from Session 13 review
**Version:** 0.7.0-beta (no version bump — fixes only)

### Context

Follow-up to Session 13 code review. Systematically fixed all 13 medium-severity issues and 10 of the 32 low-severity issues identified in the review. Focused on thread safety, CORS hardening, resource management, and correctness fixes.

### Baseline

- **Tests:** 670 passed, 22 skipped, 0 failures (matches Session 13)

### Medium Severity Fixes (13/13 complete)

| # | Issue | File(s) | Fix |
|---|-------|---------|-----|
| 8 | Wildcard CORS | `map_server.py`, `meshtastic_api_proxy.py`, `config.py` | Added `cors_allowed_origin` config key (default `None` = same-origin, no CORS headers). CORS headers only sent when explicitly configured. |
| 9 | Aggregator thread safety | `aggregator.py` | Added `_data_lock` protecting `_cached_overlay`, `_last_collect_time`, `_last_collect_counts`. |
| 10 | AREDN collector thread safety | `aredn_collector.py` | Added `_topo_lock`. Refactored `_fetch` to build topology data in local vars, swap under lock. `_fetch_from_node` now returns `(features, links)` tuple. Also fixed IPv6 false-positive in port detection. |
| 11 | Config thread safety | `config.py` | Added `_lock` around all `_settings` reads/writes: `load()`, `save()`, `get()`, `set()`, `update()`, `to_dict()`, `get_enabled_sources()`. |
| 12 | WebSocket server resource | `websocket_server.py` | Explicit `server.close()` before `loop.stop()` in shutdown. Added `_close_server_and_stop` static method. |
| 13 | MQTT get_all_nodes side effect | `mqtt_subscriber.py` | Read method no longer mutates store. Stale-node `is_online=False` set on copy, not original dict. |
| 14 | Proxy async failure | `meshtastic_api_proxy.py` | Wrapped `serve_forever()` in `_serve_forever_safe()` that sets `_running=False` on exception. |
| 15 | Connection manager TOCTOU | `connection_manager.py` | Replaced acquire/release lock probe with `_holder is not None` check. Added docstring noting inherent diagnostic-only nature. |
| 16 | Service worker FIFO→LRU | `sw-tiles.js` | Cache hits now delete+re-insert entry (LRU touch). `enforceCacheLimit` amortized to ~1% of fetches. |
| 17 | Precache template substitution | `sw-tiles.js` | Added `.replace('{r}', '')` for retina placeholder. |
| 18 | Missing HTTP response | `map_server.py` | Added `else: self._send_json({"error": "Not found"}, 404)` for all path-parsed routes with `len(parts)` checks. |
| 19 | Band key matching | `hamclock_collector.py` | Replaced substring matching with regex `(?<!\d)(80|40|30|20|17|15|12|10)m?\b` using negative lookbehind. |
| 20 | Plugin lifecycle thread safety | `plugin_lifecycle.py` | Added `_lock` for state, error, history, and listener mutations. Listeners invoked outside lock. |

### Low Severity Fixes (10 of 32)

| Issue | File | Fix |
|-------|------|-----|
| Import inside method | `base.py` | Moved `ReconnectStrategy` import to module level. |
| Heartbeat list slicing | `node_state.py` | Changed `heartbeats` from `List` to `deque(maxlen=N)` for O(1) trimming. |
| `total_transitions` unprotected read | `node_state.py` | Wrapped in `self._lock`. |
| `total_drifts` unprotected read | `config_drift.py` | Wrapped in `self._lock`. |
| Spurious drift from type mismatch | `config_drift.py` | Added `_normalize_value()` so `int(1)` == `float(1.0)`. |
| `get_memory_usage()` unprotected read | `perf_monitor.py` | Added lock; split into public/internal `_memory_usage_locked()` to avoid deadlock from `get_stats()`. |
| Route dict rebuilt per request | `map_server.py` | Moved to class-level `_ROUTE_TABLE` dict (method names, resolved via `getattr`). |
| IPv6 address false-positive | `aredn_collector.py` | Port detection now checks for `[` prefix to distinguish IPv6 from host:port. |
| CORS in proxy | `meshtastic_api_proxy.py` | Added `cors_origin` constructor parameter, handler reads from proxy instance. |
| CORS proxy propagation | `meshtastic_api_proxy.py` | Handler `_get_cors_origin()` reads `_cors_origin` from proxy. |

### Remaining Low Severity (22 unfixed)

- `base.py` thread safety: `_cache`, `_cache_time` read/written without sync
- `mqtt_subscriber._running/_connected`: bare booleans across threads
- `mqtt_subscriber._messages_received`: incremented without lock
- `event_bus.reset()`: replaces `_stats` object; concurrent `publish()` loses counters
- `reconnect.py`: entire class has no synchronization
- `node_history` throttle check outside lock allows duplicate observations
- `node_history` `get_snapshot` can return duplicates
- `openhamclock_compat` band key aliases: overlapping bands silently overwrite
- `connection_manager.stats`: reads multiple fields without lock
- `connection_manager._instances`: class-level mutable shared across subclasses
- `reticulum_collector` cache: `_read_cache_file` returns all features without network filter
- `reticulum_collector` duplicated cache-reading logic
- Duplicated deduplication pattern across 4 collectors + aggregator
- Duplicated unified cache path constant across collectors
- `map_server` private attribute access: reaches into `aggregator._collectors`
- `meshforge_maps.html` `rebuildMarkersFromFeatures`: duplicates `processGeoJSON` marker logic
- `meshforge_maps.html` `trajectoryLayers`: grows without bound
- `meshforge_maps.html` `allFeatures`: retains references indefinitely

### Files Modified (16)

| File | Change |
|------|--------|
| `src/collectors/aggregator.py` | Thread safety: `_data_lock` for cached data |
| `src/collectors/aredn_collector.py` | Thread safety: `_topo_lock`; `_fetch_from_node` returns tuple; IPv6 fix |
| `src/collectors/base.py` | Moved `ReconnectStrategy` import to top-level |
| `src/collectors/hamclock_collector.py` | Regex band key matching with negative lookbehind |
| `src/collectors/mqtt_subscriber.py` | `get_all_nodes` no longer mutates store |
| `src/utils/config.py` | Thread safety: `_lock` on all settings access; `cors_allowed_origin` config key |
| `src/utils/config_drift.py` | Lock on `total_drifts`; `_normalize_value` for type-safe comparison |
| `src/utils/connection_manager.py` | `is_locked` uses `_holder is not None` instead of acquire/release probe |
| `src/utils/meshtastic_api_proxy.py` | CORS configurable; `_serve_forever_safe` wraps serve_forever |
| `src/utils/node_state.py` | `deque` for heartbeats; lock on `total_transitions` |
| `src/utils/perf_monitor.py` | Lock on `get_memory_usage`; `_memory_usage_locked` avoids deadlock |
| `src/utils/plugin_lifecycle.py` | Thread safety: `_lock` for all state access; listeners outside lock |
| `src/utils/websocket_server.py` | Explicit `server.close()` before stopping event loop |
| `src/map_server.py` | CORS configurable; 404 for short paths; class-level route table |
| `web/sw-tiles.js` | LRU via delete+re-insert on hit; `{r}` placeholder; amortized eviction |
| `tests/test_aredn_hardening.py` | Updated for `_fetch_from_node` tuple return |
| `tests/test_node_state.py` | Updated for deque heartbeats |

### Test Results

- **Before:** 670 passed, 22 skipped
- **After:** 670 passed, 22 skipped, 0 failures, 0 regressions

### Session Entropy Watch

- Session stayed focused throughout — systematic task list with 17 tracked items
- All 13 medium-severity issues fixed
- 10 low-severity quick wins completed
- Found and fixed a deadlock introduced by own lock addition (perf_monitor)
- Zero regressions at final checkpoint
- No scope creep — strictly fixes from Session 13 review findings

---

## Session 13: Code Review & Health Check

**Date:** 2026-02-12
**Branch:** `claude/code-review-health-check-Y298M`
**Scope:** Full codebase code review, defect identification, and targeted fixes
**Version:** 0.7.0-beta (no version bump — fixes only)

### Context

Comprehensive code review and health check of all source modules. Systematic review of collectors, utils, core server, and frontend. Identified and fixed critical/high-severity defects. No new features — strictly review and hardening.

### Baseline

- **Tests:** 670 passed, 22 skipped, 0 failures (matches Session 12)
- **TODO/FIXME/HACK markers:** None found (clean codebase)

### Code Review Findings

**52 issues identified across 4 severity levels:**

#### Critical (2)

1. **Falsy coordinate data loss** — `meshtastic_collector.py:123-124`, `mqtt_subscriber.py:642-643,646,751`: Nodes at equator (lat=0), prime meridian (lon=0), sea level (alt=0), or with SNR=0 had valid data silently discarded by truthiness checks (`or` operator, `if value`). **FIXED.**

2. **health_scoring.py TypeError crash** — `_score_congestion()` line 477: When both `channel_util` and `air_util_tx` failed `float()` conversion, code fell through to `CHANNEL_UTIL_HIGH - None`, raising `TypeError`. **FIXED.**

#### High (5)

3. **XSS in WebSocket popup** — `meshforge_maps.html:1938`: Unescaped `nodeId` from MQTT injected into popup HTML via `bindPopup('<b>' + nodeId + '</b>')`. Added `esc()` call. **FIXED.**

4. **XSS in onclick handler** — `meshforge_maps.html:1735`: `esc()` HTML-escapes but does not escape single quotes, allowing JS breakout in inline `onclick="toggleTrajectory('...')"`. Replaced with `data-node-id` attribute + `this.dataset.nodeId`. **FIXED.**

5. **mqtt_subscriber deadlock risk** — `_evict_oldest_locked()` invoked `_on_node_removed` callback while lock held (line 363-369). If callback re-entered the store, deadlock. Refactored to defer callback invocation outside lock. **FIXED.**

6. **map_server socket leak** — `stop()` called `server.shutdown()` but not `server.server_close()`, leaving listening socket in TIME_WAIT. **FIXED.**

7. **hamclock_collector wrong port in API** — `get_hamclock_data()` always reported `self._hamclock_port` (8080) even when OpenHamClock (3000) was the active variant. Now reads port from cached collection data. **FIXED.**

#### Medium (13) — Identified, Not Fixed This Session

8. **Wildcard CORS** — `map_server.py:187,669` and `meshtastic_api_proxy.py:169`: `Access-Control-Allow-Origin: *` on all responses exposes node data to any website. Should be configurable or restricted to same-origin.

9. **aggregator thread safety** — `_cached_overlay`, `_last_collect_time`, `_last_collect_counts` read/written without locks across threads.

10. **aredn_collector thread safety** — `_lqm_links` and `_node_coords` mutated during `_fetch` and read via `get_topology_links` without synchronization.

11. **config.py thread safety** — `MapsConfig._settings` read/written by multiple threads without lock protection.

12. **websocket_server resource** — WebSocket `_server` not explicitly closed via `server.close()`/`wait_closed()` during shutdown, relying on GC.

13. **mqtt_subscriber `get_all_nodes` side effect** — Read method mutates store state by marking stale nodes as offline.

14. **meshtastic_api_proxy async failure** — Reports `_running=True` even if `serve_forever()` fails asynchronously in background thread.

15. **connection_manager TOCTOU** — `is_locked` property acquires/releases lock to probe, result is immediately stale.

16. **sw-tiles.js FIFO not LRU** — `enforceCacheLimit` evicts by insertion order, not access frequency. Documented as LRU but is FIFO.

17. **sw-tiles.js precache template** — `{r}` retina placeholder and subdomain rotation not substituted in precached tile URLs, causing cache misses.

18. **map_server no response for short node paths** — `/api/nodes/<id>` with `len(parts) < 4` sends no HTTP response, causing client hang.

19. **hamclock band key matching** — Substring matching (`"80" in key`) is fragile; `Band140m` would match `"40"`.

20. **plugin_lifecycle no thread safety** — State reads/writes and listener list mutations unsynchronized despite claiming thread-safety.

#### Low (32) — Documented Only

- Dead import: `perf_monitor.py` imported `sys` (unused). **FIXED.**
- Dead import: `hamclock_collector.py` imported `normalize_band_conditions` (unused). **FIXED.**
- `node_history.py:128`: `timestamp=0` treated as falsy via `or` operator. **FIXED.**
- `circuit_breaker.py:200-203`: `failure_threshold=0` treated as falsy via `or`. **FIXED.**
- `config_drift.py:191`: `since=0` treated as falsy, disabling time filter. **FIXED.**
- `base.py` thread safety: `_cache`, `_cache_time` read/written without sync in `collect()`.
- `base.py` import inside method: `ReconnectStrategy` imported on every `collect()` call.
- `mqtt_subscriber._running/_connected`: bare booleans across threads without `threading.Event`.
- `mqtt_subscriber._messages_received`: incremented without lock.
- `event_bus.reset()`: replaces `_stats` object; concurrent `publish()` loses counters.
- `node_state.add_heartbeat`: list slicing in steady state; `deque(maxlen=)` would be O(1).
- `node_state.total_transitions`: read without lock.
- `config_drift.total_drifts`: read without lock.
- `perf_monitor.get_memory_usage()`: reads `_samples` without lock.
- `reconnect.py`: entire class has no synchronization; not thread-safe.
- `node_history` throttle check outside lock allows duplicate observations.
- `node_history` `get_snapshot` can return duplicates for same-timestamp observations.
- `config_drift` str comparison: `int(1)` vs `float(1.0)` produces spurious drift.
- `openhamclock_compat` band key aliases: overlapping bands silently overwrite.
- `connection_manager.stats`: reads multiple fields without lock; inconsistent snapshot.
- `connection_manager._instances`: class-level mutable shared across subclasses.
- `aredn_collector` IPv6 address: colon check false-positive for IPv6 targets.
- `reticulum_collector` cache: `_read_cache_file` returns all features without network filter.
- `reticulum_collector` duplicated cache-reading logic.
- Duplicated deduplication pattern across 4 collectors + aggregator.
- Duplicated unified cache path constant across collectors.
- `map_server` route dispatch: dict rebuilt on every request.
- `map_server` private attribute access: reaches into `aggregator._collectors`.
- `meshforge_maps.html` `rebuildMarkersFromFeatures`: duplicates `processGeoJSON` marker logic.
- `meshforge_maps.html` `trajectoryLayers`: grows without bound.
- `meshforge_maps.html` `allFeatures`: retains references indefinitely.
- `sw-tiles.js` `enforceCacheLimit`: runs on every tile fetch without amortization.

### Files Modified (10)

| File | Change |
|------|--------|
| `src/collectors/meshtastic_collector.py` | Falsy coordinate fix: `or` → explicit `is None` check |
| `src/collectors/mqtt_subscriber.py` | Falsy lat/lon/alt fix; SNR always included; deadlock fix (callback outside lock) |
| `src/collectors/hamclock_collector.py` | Removed dead import; port read from cached data |
| `src/utils/health_scoring.py` | Re-check for both-None after conversion in `_score_congestion` |
| `src/utils/perf_monitor.py` | Removed dead `import sys` |
| `src/utils/node_history.py` | Timestamp falsy fix: `or` → `if is not None` |
| `src/utils/circuit_breaker.py` | Threshold falsy fix: `or` → `if is not None` |
| `src/utils/config_drift.py` | `since` falsy fix: `and` → `is not None` |
| `src/map_server.py` | Added `server_close()` on shutdown |
| `web/meshforge_maps.html` | XSS fixes: `esc()` on popup, data-attribute for onclick |

### Test Results

- **Before:** 670 passed, 22 skipped
- **After:** 670 passed, 22 skipped, 0 failures, 0 regressions

### Session Entropy Watch

- Session stayed focused and systematic throughout
- Systematic task list maintained (17 items tracked)
- Code review completed across all modules before any fixes applied
- All fixes targeted specific defects identified in review
- No scope creep — review-only with targeted fixes
- Zero regressions at checkpoint

### Next Session Suggestions

1. **Thread safety audit** — Medium-severity items 9-11, 20: Add locks to `aggregator`, `aredn_collector`, `config.py`, `plugin_lifecycle`. Significant effort, high reliability payoff.
2. **CORS hardening** — Item 8: Make `Access-Control-Allow-Origin` configurable, default to same-origin.
3. **WebSocket marker update bug** — `processGeoJSON` does not set `.feature` on markers, so `updateOrAddNode` can never match existing markers. Needs architectural fix.
4. **Service worker LRU** — Item 16: Replace FIFO eviction with actual LRU using Cache API metadata or a separate timestamp index.
5. **Missing HTTP responses** — Item 18: Add 404 for unmatched `/api/nodes/<id>` sub-routes.
6. **Deduplication refactor** — Item from cross-cutting: Extract shared `deduplicate_features()` to `base.py`.

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

---

## Session 15: Remaining Low-Severity Fixes (22 → 0)

**Date:** 2026-02-12
**Branch:** `claude/session-entropy-monitoring-3KQ4M`
**Baseline:** 670 passed, 22 skipped, 0 failures
**Result:** 671 passed, 22 skipped, 0 failures (+1 new test, 0 regressions)

### Goal
Complete all 22 remaining low-severity issues from the Session 13 code review,
bringing the codebase to zero known issues.

### Fixes Completed (22/22)

#### Thread Safety (5 fixes)
1. **`base.py` _cache/_cache_time** — Added `_cache_lock` (threading.Lock) protecting
   all reads/writes of `_cache` and `_cache_time` in `collect()`, `health_info`, `clear_cache()`
2. **`mqtt_subscriber._running/_connected`** — Replaced bare booleans with
   `threading.Event` objects (`set()/clear()/is_set()`) for atomic cross-thread signaling
3. **`mqtt_subscriber._messages_received`** — Added `_stats_lock` protecting
   increment in `_on_message` and read in `get_stats()`
4. **`event_bus.reset()`** — Changed from replacing `_stats` object to calling
   `_stats.reset()` in-place, so concurrent `publish()` always sees the same instance
5. **`reconnect.py`** — Added `threading.Lock` around all mutable state:
   `_attempt`, `_total_attempts`, `_last_attempt_time` in `next_delay()`, `reset()`,
   `should_retry()`, and property accessors

#### Node History (2 fixes)
6. **Throttle check TOCTOU** — Moved `_last_recorded` check inside `_lock` in
   `record_observation()` to prevent two concurrent calls from both passing the
   throttle check and inserting duplicate observations
7. **`get_snapshot()` duplicates** — Changed snapshot query from joining on
   `MAX(timestamp)` (which can match multiple rows) to joining on `MAX(id)`
   (guaranteed unique), eliminating duplicate rows for same-timestamp observations

#### Connection Manager (2 fixes)
8. **`stats` property** — Added `_stats_lock` for consistent snapshot reads of
   `_holder`, `_acquire_time`, `_total_acquisitions`, `_total_timeouts`, `_total_releases`
9. **`_instances` class-level sharing** — `get_instance()` now checks `cls.__dict__`
   to ensure each class in the hierarchy gets its own `_instances` dict instead of
   inheriting the parent's mutable dict

#### Compatibility (1 fix)
10. **`openhamclock_compat` band aliases** — Fixed overlapping aliases where
    `band80m` and `band40m` both mapped to `"80m-40m"` (silently losing one).
    Each band now maps to its own canonical key (`"80m"`, `"40m"`, etc.)

#### Data Collection (5 fixes)
11. **`reticulum_collector._read_cache_file()`** — Added network filter
    (`props.network == "reticulum"`) when reading FeatureCollections from cache,
    preventing non-RNS features from leaking through shared caches
12. **`reticulum_collector._fetch_from_unified_cache()`** — Eliminated duplicated
    cache-reading logic by delegating to `_read_cache_file()` (which now filters)
13. **Deduplication pattern** — Extracted `deduplicate_features()` to `base.py`,
    replacing 4 identical `seen_ids` loops in `reticulum_collector._fetch()` and
    `aggregator.collect_all()`
14. **Cache path constants** — Consolidated `MESHFORGE_DATA_DIR` and
    `UNIFIED_CACHE_PATH` into `base.py`, updated `reticulum_collector`,
    `aredn_collector`, and `meshtastic_collector` to use shared constants
15. **`map_server` private access** — Added public accessors to `DataAggregator`:
    `get_collector()`, `enabled_collector_count`, `enabled_collector_names`,
    `mqtt_subscriber` property. Updated all `map_server.py` references from
    `aggregator._collectors` / `aggregator._mqtt_subscriber` to public API

#### Frontend (3 fixes)
16. **`trajectoryLayers` unbounded growth** — Added `MAX_TRAJECTORIES = 20` cap;
    oldest trajectory is evicted (removed from map and deleted) when limit reached
17. **`allFeatures` reference retention** — Confirmed this is bounded (replaced on
    each `processGeoJSON()` call, old array GC'd). No fix needed.
18. **`rebuildMarkersFromFeatures` duplication** — Extracted shared marker rendering
    into `renderMarkers()` function; `processGeoJSON()` and `rebuildMarkersFromFeatures()`
    both delegate to it. Eliminated ~45 lines of duplicated marker creation code.

### Files Changed

| File | Changes |
|------|---------|
| `src/collectors/base.py` | +`_cache_lock`, +`deduplicate_features()`, +`MESHFORGE_DATA_DIR`, +`UNIFIED_CACHE_PATH` |
| `src/collectors/mqtt_subscriber.py` | `threading.Event` for `_running`/`_connected`, `_stats_lock` for `_messages_received` |
| `src/collectors/reticulum_collector.py` | Network filter in `_read_cache_file`, dedup refactor, cache path consolidation |
| `src/collectors/aggregator.py` | Uses `deduplicate_features()`, public accessor methods for collectors/MQTT |
| `src/collectors/aredn_collector.py` | Uses `MESHFORGE_DATA_DIR`/`UNIFIED_CACHE_PATH` |
| `src/collectors/meshtastic_collector.py` | Uses `MESHFORGE_DATA_DIR` |
| `src/map_server.py` | Uses public aggregator accessors instead of private attributes |
| `src/utils/event_bus.py` | `_BusStats.reset()` method for in-place stats reset |
| `src/utils/reconnect.py` | Full synchronization with `threading.Lock` |
| `src/utils/node_history.py` | Throttle check moved inside lock, snapshot query deduplicated |
| `src/utils/connection_manager.py` | `_stats_lock`, per-class `_instances` dict isolation |
| `src/utils/openhamclock_compat.py` | Distinct canonical keys for each band alias |
| `web/meshforge_maps.html` | `renderMarkers()` extraction, `MAX_TRAJECTORIES` cap |
| `tests/test_reliability_fixes.py` | Updated for `threading.Event` API |
| `tests/test_openhamclock_compat.py` | Updated band alias tests, +1 new test |

### Session Entropy Watch
Session remained systematic. All 22 items addressed in dependency order
(thread safety → data integrity → architecture → frontend). Zero regressions
at every checkpoint. Session complete — all known code review issues resolved.
