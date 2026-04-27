---
name: MeshForge Maps
description: >
  MeshForge Maps visualization plugin assistant for multi-source mesh network mapping.
  Handles Leaflet.js maps, data collectors, REST API, health scoring, alert engine,
  topology visualization, and TUI dashboard development.

  Use when working with: (1) Map visualization and Leaflet.js frontend, (2) Data collector
  development (BaseCollector pattern), (3) REST API endpoints, (4) Node health scoring,
  (5) Alert engine threshold rules, (6) WebSocket real-time updates, (7) NOAA weather alerts,
  (8) Curses TUI dashboard.

  Triggers: meshforge-maps, map_server, collector, leaflet, topology, health_scoring, alert_engine, geojson, noaa
---

# MeshForge Maps Development Assistant

## Project Context

meshforge-maps is a unified multi-source mesh network map visualization plugin.
Aggregates Meshtastic, Reticulum/RMAP, AREDN, and NOAA data into interactive maps.
Part of the MeshForge ecosystem (Visualization layer).

**Version:** 0.7.0-beta
**Owner:** WH6GXZ (Nursedude)

## Security Rules (MUST FOLLOW)

### MF001-MF004
Same as MeshForge NOC. See `.claude/rules/security.md`.

### Web Security
- HTML-escape all browser output (XSS prevention)
- API key comparison via `hmac.compare_digest()` (timing-safe)
- Network bindings default to `127.0.0.1`
- CORS disabled by default
- Security headers on all responses

## Architecture

```
src/
├── main.py              Entry point + Plugin class
├── map_server.py        HTTP server, REST API, MapServerContext
├── collectors/          Data aggregation (BaseCollector ABC)
│   ├── base.py          validate_coordinates(), make_feature()
│   ├── aggregator.py    Multi-source merge + dedup
│   ├── meshtastic_collector.py
│   ├── reticulum_collector.py
│   ├── hamclock_collector.py
│   ├── aredn_collector.py
│   ├── mqtt_subscriber.py
│   └── noaa_alert_collector.py
├── utils/               Operations & infrastructure
│   ├── config.py        MapsConfig, DEFAULT_CONFIG (SSOT)
│   ├── paths.py         get_real_home()
│   ├── health_scoring.py
│   ├── alert_engine.py
│   ├── analytics.py
│   ├── event_bus.py
│   ├── node_history.py  SQLite trajectory
│   ├── node_state.py    State machine
│   └── websocket_server.py
├── tui/                 Curses dashboard (7 tabs)
└── __main__.py
web/                     Leaflet.js frontend + service worker
```

## Key Patterns

### Collector Pattern
```python
class MyCollector(BaseCollector):
    def _fetch(self) -> dict:
        # Return GeoJSON FeatureCollection
        return make_feature_collection(features)
```

### Coordinate Validation
```python
lat, lon = validate_coordinates(raw_lat, raw_lon, convert_int=True)
if lat is not None:
    feature = make_feature(node_id, lat, lon, "meshtastic")
```

### Config (Source of Truth)
```python
from src.utils.config import MapsConfig
config = MapsConfig()
value = config.get("enable_meshtastic", True)
```

## Key Commands

```bash
python -m src.main                  # Start map server
python -m src.main --tui            # Start TUI dashboard
pytest tests/ -v                    # Run tests (982)
```

## Key Ports

| Service | Port | Protocol |
|---------|------|----------|
| Map HTTP server | 8808 | HTTP |
| WebSocket server | 8809 | WS |

## Cross-Repo Reference

See `.claude/foundations/cross_repo_topology.md` for ecosystem task delegation.
