# Security Review — meshforge-maps

Last reviewed: 2026-02-21 | Version: 0.7.0-beta

---

## Audit Summary

| Category | Rating | Notes |
|----------|--------|-------|
| Input Validation | Excellent | Coordinate validation, node ID regex, `_safe_query_param()` |
| Subprocess Usage | Secure | Single call (`rnstatus`), no `shell=True`, timeout set |
| Authentication | Good | HMAC timing-safe API key via `X-MeshForge-Key` header |
| Authorization | Good | Localhost-only default, CORS disabled by default |
| Secret Management | Good | Credentials in settings.json only; config written with restrictive umask |
| Exception Handling | Good | No bare `except:` clauses; errors logged, not leaked to clients |
| Network Security | Good | TLS auto-enabled for MQTT; safe headers; request timeouts on all HTTP calls |
| File Operations | Good | Restrictive umask on credential files; no path traversal; symlink-safe |
| SQL Injection | Excellent | All queries parameterized; no string interpolation in SQL |
| Dependencies | Strong | Optional deps degrade gracefully; no `eval`/`exec`/`pickle` |
| Web Frontend | Good | XSS mitigated via `esc()` function; CSP headers on HTML responses |

**Critical findings: 0** | **High findings: 0** | **Medium findings: 2 (remediated)** | **Low findings: 4 (remediated)**

---

## Findings

### MEDIUM — Config file permissions (remediated)

- **Location:** `src/utils/config.py:137`
- **Issue:** Settings file containing MQTT credentials was created without restrictive permissions, potentially world-readable on multi-user systems.
- **Fix:** Config save now uses `os.umask(0o077)` to ensure owner-only read/write (mode 0600).

### MEDIUM — Missing security headers (remediated)

- **Location:** `src/map_server.py:_send_json()`, `_serve_map()`
- **Issue:** HTTP responses lacked `X-Content-Type-Options`, `X-Frame-Options`, and `Content-Security-Policy` headers.
- **Fix:** All JSON responses include `X-Content-Type-Options: nosniff` and `X-Frame-Options: DENY`. HTML map page additionally includes a `Content-Security-Policy` header restricting script, style, image, and connection sources.

### LOW — SharedHealthStateReader.available race condition (remediated)

- **Location:** `src/utils/shared_health_state.py:74-77`
- **Issue:** The `available` property read `_available` and `_conn` without holding `_lock`, creating a TOCTOU window where the connection could be closed between check and use.
- **Fix:** Property now acquires `_lock` before reading state.

### LOW — Analytics encapsulation break (remediated)

- **Location:** `src/utils/analytics.py:88, 145, 198, 259`
- **Issue:** `HistoricalAnalytics` directly accessed `_history._lock` and `_history._conn` (private attributes), risking access to a closed DB connection during concurrent shutdown.
- **Fix:** Added `NodeHistoryDB.execute_read()` public method; analytics now uses it instead of reaching into private state.

### LOW — Service worker origin validation (remediated)

- **Location:** `web/sw-tiles.js` message event listener
- **Issue:** The `message` event handler did not validate `event.origin`, allowing cross-origin pages to send commands to the service worker.
- **Fix:** Added origin check that rejects messages from non-matching origins.

### INFO — No rate limiting on API endpoints

- **Location:** `src/map_server.py`
- **Status:** Not implemented. Acceptable risk for localhost-default deployments. Network-exposed deployments should use a reverse proxy with rate limiting (see Deployment Hardening below).

### INFO — systemd service binds to 0.0.0.0

- **Location:** `scripts/install.sh` (service file template)
- **Status:** The systemd service template uses `--host 0.0.0.0` while the code defaults to `127.0.0.1`. This is intentional — the service file is for Pi/server deployments that need network access. Deployers should review the bind address for their environment.

---

## Positive Findings

These security practices are already implemented and should be maintained:

- **Timing-safe API key comparison** — `hmac.compare_digest()` in `map_server.py:162` prevents timing attacks
- **Parameterized SQL everywhere** — All SQLite queries use `?` placeholders; no string interpolation in queries
- **XSS prevention** — Frontend `esc()` function (meshforge-maps.js:488) escapes all user data before HTML insertion; `innerHTML` only used with pre-escaped content
- **No shell=True** — Single subprocess call (`rnstatus`) uses list args, timeout, and proper exception handling
- **No bare except clauses** — All exception handlers catch specific types
- **Localhost-default binding** — HTTP and WebSocket servers bind to `127.0.0.1` by default
- **CORS disabled by default** — `cors_allowed_origin: None` in config; no CORS headers sent
- **Graceful dependency degradation** — `paho-mqtt`, `meshtastic`, `websockets`, `pyopenssl` all guarded with try/except; features disabled when missing, never crash
- **MQTT TLS auto-enabled** — When credentials are provided, TLS is enabled automatically with `ssl.CERT_REQUIRED`
- **Node ID validation** — Regex `^!?[0-9a-fA-F]{1,16}$` applied to all node ID API parameters
- **Coordinate validation** — `validate_coordinates()` handles NaN, Infinity, out-of-range, Null Island (0,0), and int-to-float conversion
- **No monkey-patching** — `MapServerContext` dataclass for dependency injection instead of patching stdlib objects
- **systemd hardening** — Service file includes `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp=true`

---

## Deployment Hardening Checklist

For production or network-exposed deployments:

### Network

- [ ] **Use a reverse proxy** (nginx, caddy) with TLS termination in front of the HTTP server
- [ ] **Restrict bind address** — only set `http_host: "0.0.0.0"` if the server must be reachable from the network; prefer `127.0.0.1` behind a reverse proxy
- [ ] **Configure a firewall** — allow only ports 8808 (HTTP) and 8809 (WebSocket) from trusted networks
- [ ] **Enable HTTPS** — the built-in server is HTTP-only; TLS must be provided by a reverse proxy

### Authentication

- [ ] **Set an API key** — add `"api_key": "your-secret-key"` to `settings.json`; clients send it via the `X-MeshForge-Key` HTTP header
- [ ] **Rate limit API requests** — configure at the reverse proxy level (e.g., nginx `limit_req`)

### MQTT

- [ ] **Use a private broker** with TLS — set `mqtt_broker`, `mqtt_username`, `mqtt_password` in settings; TLS is auto-enabled when credentials are provided
- [ ] **Restrict MQTT topic** — narrow `mqtt_topic` from `msh/#` to your region/channel (e.g., `msh/US/mychannel/#`)

### File Permissions

- [ ] **Verify settings.json permissions** — should be `0600` (owner read/write only); the application sets this on save, but verify after manual edits: `chmod 600 ~/.config/meshforge/plugins/org.meshforge.extension.maps/settings.json`
- [ ] **Verify database permissions** — `~/.local/share/meshforge/maps_node_history.db` should not be world-readable

### CORS

- [ ] **Leave CORS disabled** unless you need cross-origin browser access; if required, set `cors_allowed_origin` to a specific origin (not `"*"`)

---

## Reporting Security Issues

If you discover a security vulnerability in meshforge-maps, please report it responsibly:

1. **Do not** open a public GitHub issue for security vulnerabilities
2. Contact the maintainers via the [MeshForge project](https://github.com/Nursedude/meshforge) security contact
3. Include: affected version, description of the vulnerability, steps to reproduce, and potential impact
