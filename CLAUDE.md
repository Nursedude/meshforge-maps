# CLAUDE.md — Project Memory for meshforge-maps

Unified multi-source mesh network map (Meshtastic, Reticulum/RMAP, AREDN, OpenHamClock/NOAA).
Runs standalone (`python -m src.main`) or as a MeshForge extension via `manifest.json` auto-discovery.
HTTP server on `:8808`, WebSocket on `:8809`. Python 3.9+, stdlib only for core.

## Source Layout

```
src/main.py                  Entry point + MeshForge plugin class
src/map_server.py            HTTP server, REST API routes, MapServerContext
src/collectors/
  base.py                    BaseCollector ABC, validate_coordinates(), make_feature()
  aggregator.py              DataAggregator — merges all sources, dedup, timing
  meshtastic_collector.py    Meshtastic via API + MQTT + cache fallback
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
src/tui/                     Curses terminal dashboard (6 tabs)
web/                         Leaflet.js frontend, sw-tiles.js offline cache
tests/                       pytest suite (863 tests)
scripts/                     install.sh, verify.sh
```

## Key Patterns

- **Collector pattern**: All collectors extend `BaseCollector` (ABC) with built-in cache, retry with exponential backoff, and stale-cache fallback. Override `_fetch()` to return a GeoJSON `FeatureCollection`.
- **GeoJSON everywhere**: Use `make_feature()` and `make_feature_collection()` from `src/collectors/base.py` for all node data. Never build GeoJSON dicts by hand.
- **Coordinate validation**: Always use `validate_coordinates()` — handles NaN, Infinity, out-of-range, int-to-float conversion (`convert_int=True` for Meshtastic `latitudeI`), and Null Island (0,0) rejection.
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
- Config file written with restrictive umask (`0o077`) to protect MQTT credentials in `settings.json`

## Anti-Patterns (from Code Reviews)

- Don't monkey-patch attributes onto stdlib objects — use typed containers (`MapServerContext`)
- Don't add tautological tests (`assert True`, `assert x == x`) — tests must verify real behavior
- Don't duplicate validation logic — reuse `validate_coordinates()`, `validate_node_id()` from `src/collectors/base.py`
- Don't swallow exceptions silently — log at minimum (`logger.error`)
- Don't add unnecessary abstraction layers — keep it stdlib-simple
- Don't add docstrings/comments/type hints to code you didn't change
- Don't create helpers or utilities for one-time operations
- Don't use feature flags or backwards-compatibility shims — just change the code
- Don't access `_lock` or `_conn` on `NodeHistoryDB` directly — use `execute_read()` for analytics queries

## Testing

```bash
pytest tests/ -v    # 879 tests, no external deps needed
```

- Test files mirror source modules: `test_<module>.py`
- Shared fixtures in `tests/conftest.py`
- All collectors tested with mock HTTP/MQTT responses (no network needed)
- Integration tests: `test_integration_config_drift.py`, `test_alerting_delivery.py`
- Hardening tests: `test_aredn_hardening.py`, `test_reliability.py`, `test_reliability_fixes.py`

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

## Commit Convention

`feat:` | `fix:` | `docs:` | `refactor:` | `test:` — followed by concise description.
