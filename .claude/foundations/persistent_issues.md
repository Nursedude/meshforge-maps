# Persistent Issues — meshforge-maps

> **Purpose**: Track recurring issues and their fixes.
> **Last updated**: 2026-03-13

---

## Active Issues

### Live Field Testing Needed
Core logic has 982 unit tests but these features need live mesh validation:
- MQTT subscriber with real broker traffic
- Alert engine with production alert volumes
- TUI dashboard under sustained data flow
- Topology visualization with 50+ nodes
- Offline tile caching on slow networks
- Multi-source concurrent collection timing

### CSP Header Strictness (MEDIUM)
Content-Security-Policy could be tighter. Currently allows some broad sources.
**Status**: Open (SEC-08 from security audit)

---

## Resolved Issues

| Issue | Fix | Prevention |
|-------|-----|------------|
| Monkey-patching stdlib | MapServerContext dataclass | Code review |
| Tautological tests | Removed, real assertions added | CI linting |
| Config file permissions | umask 0o077 | Security audit |
| Security headers missing | Added X-Content-Type-Options, CSP | HTTP handler |
| Shared health state race | Lock-free read via shared memory | Architecture review |
| Analytics encapsulation | `execute_read()` public API | Code review |

---

## Development Checklist

Before committing:
- [ ] No `Path.home()` — use `get_real_home()` from `src/utils/paths.py`
- [ ] No bare `except:` — specific exception types
- [ ] No `shell=True` — use list args with timeout
- [ ] Coordinate validation via `validate_coordinates()` — no hand-built checks
- [ ] GeoJSON via `make_feature()` / `make_feature_collection()` — no hand-built dicts
- [ ] Config keys match `DEFAULT_CONFIG` in `src/utils/config.py`
- [ ] HTML output escaped (XSS prevention)
- [ ] Tests pass: `pytest tests/ -v`
