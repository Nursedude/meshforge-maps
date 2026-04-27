# Cross-Repo Topology — MeshForge Ecosystem

> See canonical version at `/opt/meshforge/.claude/foundations/cross_repo_topology.md`

## This Repo's Role

**meshforge-maps** is the **Visualization** layer of the MeshForge ecosystem.

### What belongs HERE:
- Interactive web maps (Leaflet.js, dark theme)
- Multi-source data aggregation (BaseCollector pattern)
- REST API for node/health/topology data (~25 endpoints)
- WebSocket real-time updates
- Per-node health scoring (battery, signal, freshness, reliability, congestion)
- Alert engine with threshold rules and multi-channel delivery
- Historical analytics (growth, heatmap, ranking)
- NOAA weather alert polygons
- Offline tile caching (service worker)
- Curses TUI dashboard (7 tabs)

### What belongs in meshforge (NOC):
- Protocol bridging, service management, RF tools, static Folium maps

### What belongs in meshing_around_meshforge:
- 12 alert type definitions, AES-256-CTR crypto, MockAPI, standalone MQTT client

## Integration with NOC

Runs standalone on `:8808`/`:8809` OR as MeshForge plugin via `manifest.json`.

```json
{"id": "org.meshforge.extension.maps", "ports": {"http": 8808, "ws": 8809}}
```

NOC discovers this plugin at startup but runs fine without it.

## Shared Security Rules

MF001-MF004 apply. See `.claude/rules/security.md` for repo-specific additions.
