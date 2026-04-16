# CLAUDE.md — Project Memory for meshforge-maps

Unified multi-source mesh network map (Meshtastic, Reticulum/RMAP, AREDN, MeshCore, OpenHamClock/NOAA).
Runs standalone (`python -m src.main`) or as a MeshForge extension via `manifest.json` auto-discovery.
HTTP server on `:8808`, WebSocket on `:8809`. Python 3.9+, stdlib only for core.

## Source Layout

```
src/main.py                  Entry point + MeshForge plugin class
src/map_server.py            HTTP server, REST API routes, MapServerContext
src/collectors/
  base.py                    BaseCollector ABC, validate_coordinates(), make_feature()
  aggregator.py              DataAggregator — merges all sources, dedup, timing
  meshtastic_collector.py    Meshtastic via API + MQTT + meshmap.net + cache
  reticulum_collector.py     Reticulum/RMAP via rnstatus + RCH REST API
  hamclock_collector.py      OpenHamClock + NOAA SWPC space weather
  aredn_collector.py         AREDN mesh nodes via sysinfo.json
  mqtt_subscriber.py         Live MQTT (paho-mqtt + protobuf decoding)
  noaa_alert_collector.py    NOAA weather alerts (api.weather.gov, EAS polygons)
src/utils/
  config.py                  MapsConfig class, DEFAULT_CONFIG dict (source of truth)
  paths.py                   get_real_home() — sudo/systemd-safe Path resolution
  health_scoring.py          NodeHealthScorer — composite 0-100 from 5 components
  alert_engine.py            Threshold rules, cooldown, multi-channel delivery
  analytics.py               HistoricalAnalytics — growth, heatmap, ranking
  event_bus.py               EventBus pub/sub (decouples components)
  node_history.py            NodeHistoryDB (SQLite trajectory)
  node_state.py              NodeStateTracker (new/stable/intermittent/offline)
  config_drift.py            Firmware/hardware change detection
  websocket_server.py        MapWebSocketServer — real-time broadcast
  plugin_lifecycle.py        PluginLifecycle state machine
  connection_manager.py      Circuit breaker registry
  reconnect.py               Exponential backoff strategy
  perf_monitor.py            Collection cycle timing, latency percentiles
  shared_health_state.py     Cross-process health via shared memory
  meshtastic_api_proxy.py    meshtasticd-compatible JSON proxy
  openhamclock_compat.py     Port detection (3000 first, 8080 fallback)
src/tui/                     Curses terminal dashboard (7 tabs)
web/                         Leaflet.js frontend, sw-tiles.js offline cache
tests/                       pytest suite (1047 tests)
scripts/                     install.sh, verify.sh
.github/workflows/ci.yml     Ruff lint + security scan, syntax check, pytest 3.9+3.11,
                             coverage ≥70%, tee'd pytest log + failure-summary PR comment
```

## Key Patterns

- **Collector pattern**: All collectors extend `BaseCollector` (ABC) with built-in cache, retry with exponential backoff, and stale-cache fallback. Override `_fetch()` to return a GeoJSON `FeatureCollection`.
- **GeoJSON everywhere**: Use `make_feature()` and `make_feature_collection()` from `src/collectors/base.py` for all node data. Never build GeoJSON dicts by hand.
- **Coordinate validation**: Always use `validate_coordinates()` — handles NaN, Infinity, out-of-range, int-to-float conversion (`convert_int=True` for Meshtastic `latitudeI`), and Null Island (0,0) rejection.
- **HTTP body caps**: For any third-party or public-internet fetch, use `bounded_read(resp, max_bytes=...)` from `src/collectors/base.py`. A bare `resp.read()` lets a compromised mirror or on-path attacker exhaust RAM. Default cap is 10 MB.
- **Online detection**: `is_node_online(last_heard, network)` from `src/collectors/base.py` rejects future timestamps (a hostile broker could otherwise forge `last_heard` in the future to pin nodes "online") and returns `None` for unknown networks.
- **Config**: `MapsConfig` in `src/utils/config.py`. `DEFAULT_CONFIG` dict is canonical. Settings persist to `~/.config/meshforge/plugins/org.meshforge.extension.maps/settings.json`.
- **Paths**: Use `get_real_home()` from `src/utils/paths.py`, never `Path.home()` directly (returns `/root` under sudo/systemd).
- **Dependency injection**: `MapServerContext` dataclass in `map_server.py` holds all server dependencies. No monkey-patching stdlib objects.
- **EventBus**: Pub/sub via `EventBus` in `src/utils/event_bus.py`. Components subscribe to typed events (`EventType` enum). Alerts, topology changes, telemetry all flow through this.
- **Optional deps degrade gracefully**: `paho-mqtt`, `meshtastic`, `websockets`, `pyopenssl` — features silently disabled when missing, never crash.

## Security Rules

- No `shell=True` in subprocess calls
- No bare `except:` — always catch specific exceptions
- No `os.system()` — use `subprocess` module
- HTML-escape all output rendered in browser (XSS prevention)
- API key comparison via `hmac.compare_digest()` (timing-safe), not `==`
- Network bindings default to `127.0.0.1` — `0.0.0.0` only when user explicitly configures it
- Validate all user inputs; node IDs validated with `NODE_ID_RE` regex (`^!?[0-9a-fA-F]{1,16}$`) from `src/collectors/base.py`
- No secrets in code — MQTT credentials come from settings.json only
- CORS disabled by default (`cors_allowed_origin: None` in config)
- Query parameters extracted via `_safe_query_param()` helper — never access raw query dicts
- HTTP responses include `X-Content-Type-Options: nosniff` and `X-Frame-Options: DENY`
- HTML responses include `Content-Security-Policy` header restricting script/style/image/connect sources
- Config file written with restrictive umask (`0o077`) to protect MQTT credentials in `settings.json`; `install.sh` wraps its seed heredoc in `(umask 077; cat > … <<EOF)` so the file is never briefly world-readable
- Bound every outbound HTTP body with `bounded_read(resp, max_bytes=...)` (from `src/collectors/base.py`). All collector HTTP fetches go through this helper (10 MB default cap); the PyPI fetch in `map_server._serve_dependencies` uses a 2 MB cap + `threading.Lock` on the shared cache. Keep new fetches on this pattern — `grep "resp\.read()"` across `src/` should stay empty
- Strings from untrusted broker payloads (MapReport `long_name`, `firmware_version`, `region`, `modem_preset`, etc.) are truncated to small per-field caps before persisting
- WebSocket control frames (opcode ≥ 0x8, i.e. ping/pong/close) are capped at 125 bytes per RFC 6455 §5.5; data frames at 1 MB. Library `_ws.connect()` uses `open_timeout=10` so a stalled upgrade can't hang the coroutine
- TUI stderr log is opened with `O_NOFOLLOW` + mode `0o600` — library output (paho-mqtt, meshtastic) can include MQTT credentials, so a planted symlink must not redirect the file and the log must not be world-readable
- `meshtastic_api_proxy` has no authentication; it warns loudly on non-loopback bind since non-loopback exposes the full MQTT node store
- CI gates (`ruff check`, security-scan `ruff --select S`, pytest 3.9+3.11, coverage ≥70%) run on every PR; failures post an extracted summary as a PR comment via the `tee /tmp/pytest.log` → `gh pr comment` pattern

## Anti-Patterns (from Code Reviews)

- Don't monkey-patch attributes onto stdlib objects — use typed containers (`MapServerContext`)
- Don't add tautological tests (`assert True`, `assert x == x`) — tests must verify real behavior
- Don't duplicate validation logic — reuse `validate_coordinates()`, `validate_node_id()` from `src/collectors/base.py`
- Don't swallow exceptions silently — log at minimum (`logger.error`); in `try/except/pass` patterns, replace with `logger.debug("%s", exc)`
- Don't add unnecessary abstraction layers — keep it stdlib-simple
- Don't add docstrings/comments/type hints to code you didn't change
- Don't create helpers or utilities for one-time operations
- Don't use feature flags or backwards-compatibility shims — just change the code
- Don't access `_lock` or `_conn` on `NodeHistoryDB` directly — use `execute_read()` for analytics queries
- Don't call `socket.setdefaulttimeout()` — it mutates a process-global that concurrent threads inherit. Use per-socket timeout (`socket.create_connection((host, port), timeout=N)`) or the library's own timeout parameter
- Don't use `0` as a cache-invalidation sentinel for values compared against `time.monotonic()` — fresh CI runners/containers can have small monotonic values, making `time.monotonic() - 0 < TTL` return stale cached data. Use `float("-inf")` instead
- Don't `resp.read()` without a byte limit on third-party or public-internet HTTP responses — use `bounded_read()`

## Testing

```bash
pytest tests/ -v    # 1047 tests, no external deps needed
ruff check src/ tests/     # matches CI's Lint & Security Check job
ruff check src/ --select S --ignore S101,S310,S603,S607   # matches Security Scan job
```

- Test files mirror source modules: `test_<module>.py`
- Shared fixtures in `tests/conftest.py`
- All collectors tested with mock HTTP/MQTT responses (no network needed)
- Integration tests: `test_integration_config_drift.py`, `test_alerting_delivery.py`
- Hardening tests: `test_aredn_hardening.py`, `test_reliability.py`, `test_reliability_fixes.py`
- Regression tests for security fixes: `TestIsNodeOnline` (clock-skew / unknown-network) and `TestBoundedRead` in `tests/test_base.py`
- Ruff config lives in `pyproject.toml`: `S310` (urlopen audit, acknowledged stdlib HTTP pattern) and `S104` (bind-all, covered by dedicated config tests) are ignored globally; tests also ignore `S101`, `S106`, `S108`

## Known Gotchas

- **Path.home() under sudo**: Returns `/root` — always use `get_real_home()` from `src/utils/paths.py`
- **Meshtastic latitudeI**: Integer fields (lat * 1e7) — pass `convert_int=True` to `validate_coordinates()`
- **Null Island**: (0, 0) coordinates are rejected as invalid GPS (common protobuf default value)
- **HamClock ports**: OpenHamClock `:3000` is preferred; legacy HamClock `:8080` is fallback only (no longer actively developed)
- **pyopenssl pinning**: Must be `>=25.3.0` with `cryptography>=45.0.7,<47` to avoid SSL conflicts
- **Graceful degradation**: All optional imports must be try/except guarded — feature disabled, never crash
- **Circuit breakers**: Per-source failure isolation via `ConnectionManager` — don't bypass for "reliability"
- **Config keys in README**: `cors_allowed_origin`, `api_key`, `enable_noaa_alerts`, `noaa_alerts_area`, `noaa_alerts_severity` are all in `DEFAULT_CONFIG` — keep README config table in sync when adding/removing keys
- **NodeHistoryDB.execute_read()**: Public API for read-only analytics queries — use this instead of accessing `_lock`/`_conn` directly
- **time.monotonic() on fresh runners**: Starts small on fresh CI runners/containers — use `float("-inf")` as the stale-cache sentinel (see `NodeHistoryDB._count_cache_time`)
- **Future timestamps are hostile input**: MQTT `last_heard` can be forged; `is_node_online()` rejects negative ages and unknown networks return `None`
- **CI diagnostic scope**: The `tee /tmp/pytest.log` + PR-comment steps are scoped to `steps.pytest.outcome == 'failure'`. Keep pytest (including coverage) inside that one step — splitting into a second pytest invocation bypasses the diagnostic

## Commit Convention

`feat:` | `fix:` | `docs:` | `refactor:` | `test:` — followed by concise description.
