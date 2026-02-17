# meshforge-maps

Visualization plugin for the [MeshForge ecosystem](https://github.com/Nursedude/meshforge/blob/main/.claude/foundations/meshforge_ecosystem.md)

> **Read the white paper:** [Building MeshForge Maps -- AI-Assisted Mesh Network Cartography](https://nursedude.substack.com/p/building-meshforge-maps)

![Version](https://img.shields.io/badge/version-0.7.0--beta-blue)
![Status](https://img.shields.io/badge/status-beta-orange)
![License](https://img.shields.io/badge/license-GPL--3.0-green)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Tests](https://img.shields.io/badge/tests-835%20passing-brightgreen)
![MeshForge](https://img.shields.io/badge/meshforge-extension-4fc3f7)

A unified multi-source mesh network map that aggregates Meshtastic, Reticulum/RMAP, OpenHamClock propagation data, and AREDN into a single configurable Leaflet.js web map with live MQTT subscription, topology visualization, per-node health scoring, threshold-based alerting, historical analytics, and offline tile caching.

**Runs standalone** or as a [MeshForge](https://github.com/Nursedude/meshforge) extension via plugin auto-discovery.

> This repo can be installed as an extension of [Nursedude/meshforge](https://github.com/Nursedude/meshforge). MeshForge discovers it automatically via `manifest.json` on launch. No MeshForge core dependency is required -- meshforge-maps runs independently with its own HTTP server.

## Features

### Data Collection
- **Multi-source data aggregation** -- collects node data from Meshtastic (MQTT/meshtasticd), Reticulum (rnstatus/RMAP/RCH), AREDN (sysinfo API), and OpenHamClock/NOAA propagation feeds
- **Live MQTT subscription** -- real-time Meshtastic node tracking via `mqtt.meshtastic.org` with protobuf decoding (POSITION_APP, NODEINFO_APP, TELEMETRY_APP, NEIGHBORINFO_APP)
- **Reticulum Community Hub (RCH) integration** -- telemetry proxy via FreeTAKTeam's FastAPI northbound REST API
- **AREDN mesh node discovery** -- per-node sysinfo API with LQM (Link Quality Manager) topology link extraction
- **Circuit breakers** -- per-source failure isolation with automatic recovery

### Visualization
- **Topology/link visualization** -- D3.js-powered mesh link overlay showing node-to-node connections with SNR-based coloring
- **Network-specific layer toggles** -- show/hide Meshtastic (green), Reticulum (purple), AREDN (orange) independently
- **Node health overlay** -- color-codes markers by composite health score (excellent/good/fair/poor/critical)
- **Space weather overlay** -- solar flux index, Kp index, solar wind speed, HF band condition assessment from NOAA SWPC
- **Propagation panel** -- VOACAP band predictions with reliability bars and SNR values, DE/DX station info, DX spots (from OpenHamClock)
- **Solar terminator** -- real-time day/night boundary overlay
- **Marker clustering** -- toggleable clustering for dense node areas
- **Node history** -- trajectory tracking and historical position playback

### Alerting & Notifications
- **Threshold-based alert engine** -- configurable rules for battery low/critical, signal poor, congestion high, health degraded, and node offline conditions with per-node cooldown throttling
- **Multi-channel delivery** -- alerts delivered via MQTT publish (base topic + severity sub-topics), webhooks (HTTP POST), EventBus events, and direct callbacks
- **Real-time alert panel** -- collapsible browser panel showing live alerts with severity-colored indicators, node IDs, timestamps, and toast notifications for critical alerts
- **Alert management** -- acknowledge alerts, filter by severity/node, configurable rules with enable/disable/cooldown

### Historical Analytics
- **Network growth time-series** -- unique nodes per time bucket showing mesh expansion over time
- **Activity heatmap** -- observation counts by hour of day (0-23) with peak activity detection
- **Node activity ranking** -- most active nodes ranked by observation count with uptime duration
- **Network summary** -- per-network breakdown of nodes and observations with averages
- **Alert trend aggregation** -- alerts bucketed over time with per-severity counts (critical/warning/info)

### Operations
- **Per-node health scoring** -- composite 0-100 score from battery, signal (SNR + hops), data freshness, connectivity reliability, and channel congestion
- **Performance profiling** -- collection cycle timing with per-source latency percentiles (p50/p90/p99), cache hit ratios
- **Node connectivity state machine** -- classifies nodes as new/stable/intermittent/offline based on heartbeat patterns
- **Config drift detection** -- tracks firmware and hardware changes across nodes
- **WebSocket real-time updates** -- event bus pushes position, telemetry, topology, and alert events to connected clients
- **Meshtastic API proxy** -- serves meshtasticd-compatible JSON endpoints for tool interoperability

### Infrastructure
- **Offline tile caching** -- service worker (sw-tiles.js) caches map tiles for offline/field use with LRU eviction
- **Configurable tile layers** -- CartoDB Dark, OpenStreetMap, OpenTopoMap, Esri Satellite, Esri Topo, Stadia Terrain
- **OpenHamClock auto-detection** -- tries port 3000 first (OpenHamClock), falls back to port 8080 (HamClock legacy)
- **Dark theme** -- matches MeshForge core UI (dark CartoDB + cyan accents)
- **Zero required dependencies** -- stdlib only; paho-mqtt and meshtastic are optional for live MQTT

## System Architecture

```mermaid
graph TB
    subgraph External["External Data Sources"]
        MQTT["mqtt.meshtastic.org<br/>msh/# topics"]
        MESHTD["meshtasticd<br/>:4403 HTTP API"]
        RNS["rnstatus<br/>local RNS instance"]
        RCH["Reticulum Community Hub<br/>FastAPI :8000"]
        NOAA["NOAA SWPC<br/>Space Weather APIs"]
        HAMCLK["OpenHamClock :3000<br/>HamClock :8080 (legacy)"]
        AREDN_NODES["AREDN Mesh Nodes<br/>sysinfo.json API"]
    end

    subgraph Core["meshforge-maps Core"]
        MQTTSUB["MQTTSubscriber<br/>paho-mqtt + protobuf"]
        STORE["MQTTNodeStore<br/>thread-safe in-memory"]
        MC["MeshtasticCollector"]
        RC["ReticulumCollector"]
        HC["HamClockCollector"]
        AC["AREDNCollector"]
        AGG["DataAggregator<br/>merge + dedup + timing"]
        CFG["MapsConfig<br/>settings.json"]
        CB["CircuitBreakerRegistry"]
        PERF["PerfMonitor<br/>latency + cache stats"]
    end

    subgraph Operations["Operations Layer"]
        HEALTH["NodeHealthScorer<br/>0-100 composite score"]
        STATE["NodeStateTracker<br/>new/stable/intermittent/offline"]
        DRIFT["ConfigDriftDetector"]
        HISTORY["NodeHistoryDB<br/>SQLite trajectory"]
        EBUS["EventBus<br/>pub/sub decoupling"]
        ALERT["AlertEngine<br/>threshold rules + cooldown"]
        ANALYTICS["HistoricalAnalytics<br/>time-series aggregation"]
    end

    subgraph Server["HTTP Server :8808 + WebSocket :8809"]
        HANDLER["MapRequestHandler"]
        API_GEO["/api/nodes/geojson"]
        API_TOPO["/api/topology/geojson"]
        API_HEALTH["/api/node-health"]
        API_ALERT["/api/alerts"]
        API_ANALYTICS["/api/analytics/*"]
        API_STAT["/api/status"]
        WS["MapWebSocketServer<br/>real-time broadcast"]
    end

    subgraph Frontend["Leaflet.js Frontend"]
        MAP["Map View<br/>Leaflet + MarkerCluster"]
        TOPO["Topology Overlay<br/>SNR-colored links"]
        HOVR["Health Overlay<br/>score-colored markers"]
        ALERTPANEL["Alert Panel<br/>live alerts + badge"]
        SW["sw-tiles.js<br/>Offline Tile Cache"]
        PANEL["Control Panel<br/>layers, weather, health, alerts"]
    end

    MQTT -->|ServiceEnvelope protobuf| MQTTSUB
    MQTTSUB --> STORE
    STORE --> MC
    MESHTD --> MC
    RNS --> RC
    RCH -->|REST API| RC
    NOAA --> HC
    HAMCLK --> HC
    AREDN_NODES --> AC

    MC --> AGG
    RC --> AGG
    HC --> AGG
    AC --> AGG
    CFG --> AGG
    CB --> AGG
    PERF --> AGG

    AGG --> EBUS
    EBUS --> STATE
    EBUS --> DRIFT
    EBUS --> HISTORY
    EBUS --> ALERT
    EBUS --> WS
    ALERT -->|MQTT publish| MQTT
    ALERT -->|ALERT_FIRED| EBUS

    HISTORY --> ANALYTICS
    ALERT --> ANALYTICS

    AGG --> HANDLER
    HANDLER --> API_GEO
    HANDLER --> API_TOPO
    HANDLER --> API_HEALTH
    HANDLER --> API_ALERT
    HANDLER --> API_ANALYTICS
    HANDLER --> API_STAT
    HEALTH --> API_HEALTH

    API_GEO --> MAP
    API_TOPO --> TOPO
    API_HEALTH --> HOVR
    API_ALERT --> ALERTPANEL
    WS -->|alert.fired| ALERTPANEL
    WS --> MAP
    SW -.->|cache-first| MAP
```

## Data Flow

```mermaid
sequenceDiagram
    participant Browser
    participant WS as WebSocket :8809
    participant Server as MapServer :8808
    participant Agg as DataAggregator
    participant Alert as AlertEngine
    participant MQTT as MQTT Broker

    Browser->>Server: GET /api/nodes/geojson
    Server->>Agg: collect_all()

    par Parallel Collection
        Agg->>Agg: meshtasticd + MQTT + RCH + NOAA
    end

    Agg-->>Server: Merged GeoJSON FeatureCollection
    Server-->>Browser: 200 OK (GeoJSON)

    Note over Agg,Alert: Telemetry triggers alert evaluation

    Agg->>Alert: evaluate_node(props, health_score)
    Alert->>Alert: Check rules + cooldown
    alt Alert triggered
        Alert->>MQTT: publish(meshforge/alerts/{severity})
        Alert->>WS: EventBus ALERT_FIRED
        WS-->>Browser: {"type":"alert.fired","data":{...}}
        Note over Browser: Alert panel updates +<br/>toast for critical alerts
    end

    Browser->>Server: GET /api/analytics/growth
    Server-->>Browser: Time-series buckets (unique nodes, observations)

    Browser->>Server: GET /api/alerts/active
    Server-->>Browser: Unacknowledged alerts

    Note over Browser: Renders markers + topology +<br/>health overlay + alert panel
```

## Node Health Scoring

Each node receives a composite health score (0-100) computed from available telemetry:

| Component | Weight | Inputs | Scoring |
|-----------|--------|--------|---------|
| **Battery** | 0-25 | Battery %, voltage | Linear: 20% = 0, 80% = full; 3.0V = 0, 3.7V = full |
| **Signal** | 0-25 | SNR (dB), hop count | Linear: -10dB = 0, 8dB = full; 7 hops = 0, 0 hops = full |
| **Freshness** | 0-20 | Last seen timestamp | Linear: 1hr ago = 0, 5min ago = full |
| **Reliability** | 0-15 | Connectivity state | Stable = 15, new = 10.5, intermittent = 4.5, offline = 0 |
| **Congestion** | 0-15 | Channel util %, TX air time | Inverted linear: 75% = 0, 25% = full |

Scores normalize to 0-100 based on available components only -- a node reporting only battery and freshness is scored out of 45 (25+20) and scaled proportionally. Not all mesh networks report all metrics.

| Score | Status | Color |
|-------|--------|-------|
| 80-100 | Excellent | Green |
| 60-79 | Good | Light green |
| 40-59 | Fair | Orange |
| 20-39 | Poor | Red |
| 0-19 | Critical | Dark red |

## Alert Delivery

Alerts are generated by the threshold-based alert engine when node telemetry crosses configured rules. Each alert is delivered through multiple channels simultaneously:

```mermaid
flowchart LR
    subgraph Trigger["Alert Evaluation"]
        TEL["Node Telemetry"] --> ENGINE["AlertEngine<br/>rule check + cooldown"]
    end
    subgraph Delivery["Multi-Channel Delivery"]
        ENGINE --> CB["Callback<br/>(in-process)"]
        ENGINE --> MQ["MQTT Publish<br/>meshforge/alerts/{severity}"]
        ENGINE --> WH["Webhook<br/>HTTP POST"]
        ENGINE --> EB["EventBus<br/>ALERT_FIRED"]
        EB --> WS["WebSocket<br/>→ browser alert panel"]
    end
```

### Default Alert Rules

| Rule | Type | Severity | Metric | Condition | Cooldown |
|------|------|----------|--------|-----------|----------|
| `battery_low` | battery_low | WARNING | battery | <= 20% | 10 min |
| `battery_critical` | battery_critical | CRITICAL | battery | <= 5% | 10 min |
| `signal_poor` | signal_poor | WARNING | snr | <= -10 dB | 10 min |
| `congestion_high` | congestion_high | WARNING | channel_util | >= 75% | 10 min |
| `health_degraded` | health_degraded | WARNING | health_score | <= 20 | 10 min |

### MQTT Alert Topics

When an MQTT client is configured, alerts publish to two topics per alert:

- **`meshforge/alerts`** -- all alerts (subscribe for full feed)
- **`meshforge/alerts/{severity}`** -- filtered by severity (`critical`, `warning`, `info`)

Alert payloads are JSON:

```json
{
  "alert_id": "alert-42",
  "rule_id": "battery_critical",
  "alert_type": "battery_critical",
  "severity": "critical",
  "node_id": "!a1b2c3d4",
  "metric": "battery",
  "value": 3.0,
  "threshold": 5.0,
  "message": "Battery level is critical (<=5%) — node !a1b2c3d4: battery=3.0",
  "timestamp": 1707752345.123,
  "acknowledged": false
}
```

## Collector Priority

```mermaid
flowchart LR
    subgraph Meshtastic
        M1["1. meshtasticd API"] --> M2["2. Live MQTT"] --> M3["3. MQTT Cache File"]
    end
    subgraph Reticulum
        R1["1. rnstatus --json"] --> R2["2. RCH API"] --> R3["3. RNS Cache"] --> R4["4. Unified Cache"]
    end
    subgraph AREDN
        A1["1. Node sysinfo API"] --> A2["2. AREDN Cache"] --> A3["3. Unified Cache"]
    end
    subgraph Propagation
        H1["1. OpenHamClock :3000"] --> H2["2. HamClock :8080 (legacy)"] --> H3["3. NOAA SWPC APIs"]
    end
```

## Data Sources

| Source | Protocol | Data | Status |
|--------|----------|------|--------|
| **Meshtastic** | HTTP API (meshtasticd :4403) + Live MQTT + cache | Node positions, telemetry, battery, SNR, neighbors | Active |
| **Reticulum/RMAP** | rnstatus --json + RCH REST API + node cache | RNS interfaces, node types, transport info | Active |
| **OpenHamClock/NOAA** | OpenHamClock REST API (:3000) + NOAA SWPC APIs | VOACAP predictions, solar flux, Kp, band conditions, DX spots | Active |
| **AREDN** | sysinfo.json per-node API + LQM + cache | Node locations, firmware, link quality metrics | Active |

### Meshtastic (Live MQTT)

Real-time node tracking via the public Meshtastic MQTT broker at `mqtt.meshtastic.org`. Subscribes to `msh/#` topic tree and decodes `ServiceEnvelope` protobuf packets. Processes POSITION_APP, NODEINFO_APP, TELEMETRY_APP, and NEIGHBORINFO_APP for live map updates and topology links.

**Optional dependencies:** `paho-mqtt`, `meshtastic` (for protobuf). Falls back to JSON mode or cache file without them.

Reference: [meshtastic.org/docs/software/integrations/mqtt](https://meshtastic.org/docs/software/integrations/mqtt/) | [liamcottle/meshtastic-map](https://github.com/liamcottle/meshtastic-map)

### Reticulum / RMAP / RCH

Local RNS path table via `rnstatus -d --json` and [Reticulum Community Hub (RCH)](https://github.com/FreeTAKTeam/Reticulum-Telemetry-Hub) FastAPI endpoints. [RMAP.world](https://rmap.world) tracks ~306 Reticulum nodes globally. See [Discussion #743](https://github.com/markqvist/Reticulum/discussions/743).

### OpenHamClock / Propagation

Space weather from [NOAA SWPC](https://services.swpc.noaa.gov/) public JSON APIs. [OpenHamClock](https://github.com/accius/openhamclock) is the recommended propagation data source -- auto-detected on port 3000. Legacy HamClock (port 8080) is supported as a fallback but is no longer in active development.

### AREDN

Per-node sysinfo API at `http://<node>.local.mesh/a/sysinfo?lqm=1`. Requires mesh network access. LQM (Link Quality Manager) data provides topology links with SNR and quality metrics between nodes. Reference: [AREDN World Map](https://worldmap.arednmesh.org/) | [AREDN docs](https://docs.arednmesh.org/en/latest/arednHow-toGuides/devtools.html)

## Installation

### Standalone

```bash
git clone https://github.com/Nursedude/meshforge-maps.git
cd meshforge-maps
python -m src.main
# Opens http://127.0.0.1:8808 (map) + ws://127.0.0.1:8809 (real-time)
```

No external Python dependencies required for core functionality -- uses only stdlib (`http.server`, `json`, `urllib`, `subprocess`, `threading`, `sqlite3`).

### As MeshForge Extension

meshforge-maps can run as a [MeshForge](https://github.com/Nursedude/meshforge) extension. MeshForge discovers it automatically via `manifest.json` on launch -- no core dependency is required, and the maps server runs its own HTTP endpoint independently.

```bash
git clone https://github.com/Nursedude/meshforge-maps.git \
    ~/.config/meshforge/plugins/meshforge-maps/

# MeshForge will auto-discover via manifest.json on next launch
```

When running as a MeshForge extension:
- MeshForge discovers `manifest.json` at startup and loads the extension
- Maps launches its own HTTP server on port 8808 (configurable) + WebSocket on 8809
- Configuration is stored at `~/.config/meshforge/plugins/org.meshforge.extension.maps/settings.json`
- The extension operates independently -- MeshForge core is not required at runtime

### Optional Dependencies

```bash
# Live MQTT: real-time Meshtastic node tracking + alert publishing
pip install paho-mqtt meshtastic

# WebSocket: real-time map updates + live alert delivery to browser
pip install websockets
```

All optional dependencies degrade gracefully -- features that require them are silently disabled when the libraries are not installed.

### Upgrading

```bash
cd meshforge-maps
git pull origin main
```

No database migrations are needed -- the SQLite node history database schema is forward-compatible. New features (alerting, analytics) activate automatically on upgrade. Configuration is preserved in `settings.json`.

## Supported Hardware

MeshForge Maps runs on any platform with Python 3.9+. It is lightweight (stdlib only, in-memory storage) and well suited for single-board computers commonly used in mesh networking deployments.

### Raspberry Pi

| Model | SoC | RAM | Status | Notes |
|-------|-----|-----|--------|-------|
| **Raspberry Pi 5** | BCM2712 (Cortex-A76) | 2/4/8 GB | Recommended | Best performance for multi-source collection + MQTT |
| **Raspberry Pi 4 Model B** | BCM2711 (Cortex-A72) | 1/2/4/8 GB | Recommended | Most common deployment target |
| **Raspberry Pi 400** | BCM2711 (Cortex-A72) | 4 GB | Supported | Keyboard form factor, same SoC as Pi 4 |
| **Raspberry Pi 3 Model B+** | BCM2837B0 (Cortex-A53) | 1 GB | Supported | Adequate for single-source or cached operation |
| **Raspberry Pi 3 Model B** | BCM2837 (Cortex-A53) | 1 GB | Supported | Adequate for single-source or cached operation |
| **Raspberry Pi Zero 2 W** | RP3A0 (Cortex-A53) | 512 MB | Supported | Quad-core; suitable for headless/field deployments |
| **Raspberry Pi Zero W** | BCM2835 (ARM1176) | 512 MB | Limited | Single-core; functional but slow with live MQTT |

> **Minimum:** 512 MB RAM, ARMv7+ (armhf) or ARM64 (aarch64). Any Pi with a quad-core SoC handles all four collectors + live MQTT comfortably.

### Supported Operating Systems

| OS | Version | Python | Status |
|----|---------|--------|--------|
| **Raspberry Pi OS (Bookworm)** | Debian 12 based | 3.11 | Recommended |
| **Raspberry Pi OS (Bullseye)** | Debian 11 based | 3.9 | Supported |
| **Ubuntu Server** | 22.04 / 24.04 LTS (ARM64) | 3.10 / 3.12 | Supported |
| **DietPi** | Latest (Bookworm based) | 3.11 | Supported |
| **Armbian** | Bookworm / Jammy | 3.11 / 3.10 | Supported |
| **Debian** | 12 Bookworm+ (x86_64/ARM) | 3.11 | Supported |
| **macOS** | 13+ (Ventura) | 3.9+ (Homebrew/system) | Supported |
| **Windows** | 10/11 | 3.9+ (python.org) | Supported |

> **Not supported:** Raspberry Pi OS Legacy (Buster / Debian 10) ships Python 3.7 which is below the 3.9 minimum. Upgrade to Bookworm or install Python 3.9+ manually.

### Desktop / Server

MeshForge Maps also runs on any x86_64 or ARM64 machine with Python 3.9+. No OS-specific dependencies -- Linux, macOS, and Windows are all supported for development and deployment.

## Configuration

Settings stored at `~/.config/meshforge/plugins/org.meshforge.extension.maps/settings.json` (when running as MeshForge extension) or passed via config dict (standalone):

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `default_tile_provider` | choice | `carto_dark` | Map tile style |
| `enable_meshtastic` | bool | `true` | Enable Meshtastic data source |
| `enable_reticulum` | bool | `true` | Enable Reticulum/RMAP source |
| `enable_hamclock` | bool | `true` | Enable propagation data (OpenHamClock/NOAA) |
| `enable_aredn` | bool | `true` | Enable AREDN source |
| `map_center_lat` | number | `20.0` | Default map center latitude |
| `map_center_lon` | number | `-100.0` | Default map center longitude |
| `map_default_zoom` | number | `4` | Default zoom level |
| `cache_ttl_minutes` | number | `15` | Data cache lifetime |
| `http_port` | number | `8808` | Map server HTTP port |
| `hamclock_host` | string | `localhost` | OpenHamClock/HamClock host |
| `openhamclock_port` | number | `3000` | OpenHamClock port (tried first) |
| `hamclock_port` | number | `8080` | HamClock legacy port (fallback) |
| `mqtt_broker` | string | `mqtt.meshtastic.org` | MQTT broker hostname |
| `mqtt_port` | number | `1883` | MQTT broker port |
| `mqtt_topic` | string | `msh/#` | MQTT subscription topic |
| `mqtt_username` | string | `null` | MQTT auth username (private brokers) |
| `mqtt_password` | string | `null` | MQTT auth password (private brokers) |
| `mqtt_alert_topic` | string | `meshforge/alerts` | MQTT topic for alert publishing |

## API Endpoints

### Node Data

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Map HTML page |
| `/api/nodes/geojson` | GET | All nodes (aggregated GeoJSON FeatureCollection) |
| `/api/nodes/<source>` | GET | Single source GeoJSON (meshtastic, reticulum, aredn) |
| `/api/nodes/<id>/trajectory` | GET | Node position history (GeoJSON LineString) |
| `/api/nodes/<id>/history` | GET | Node observation history (timestamps, positions) |
| `/api/nodes/<id>/health` | GET | Per-node health score with component breakdown |

### Topology & Overlays

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/topology` | GET | Mesh link/neighbor data (JSON) |
| `/api/topology/geojson` | GET | Topology as GeoJSON (SNR-colored LineStrings) |
| `/api/overlay` | GET | Space weather + solar terminator data |
| `/api/hamclock` | GET | OpenHamClock/NOAA propagation (VOACAP, DX spots, band conditions) |

### Alerting

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/alerts` | GET | Alert history (supports `?severity=`, `?node_id=`, `?limit=` filters) |
| `/api/alerts/active` | GET | Unacknowledged alerts |
| `/api/alerts/rules` | GET | Configured alert rules |
| `/api/alerts/summary` | GET | Alert statistics (total fired, active, by severity/type, MQTT stats) |

### Historical Analytics

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/analytics/growth` | GET | Network growth time-series (`?since=`, `?until=`, `?bucket=` in seconds) |
| `/api/analytics/activity` | GET | Activity heatmap by hour of day (`?since=`, `?until=`) |
| `/api/analytics/ranking` | GET | Most active nodes (`?since=`, `?limit=`) |
| `/api/analytics/summary` | GET | High-level network statistics (`?since=`) |
| `/api/analytics/alert-trends` | GET | Alert trend aggregation (`?bucket=` in seconds) |

### Health & Monitoring

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/node-health` | GET | Health scores for all nodes |
| `/api/node-health/summary` | GET | Aggregate health statistics (avg, min, max, status counts) |
| `/api/health` | GET | System health score (0-100) with freshness, source, circuit breaker breakdown |
| `/api/status` | GET | Server status (uptime, data age, MQTT, WebSocket, event bus stats) |
| `/api/perf` | GET | Performance profiling (per-source latency p50/p90/p99, cache hit ratio) |
| `/api/node-states` | GET | Node connectivity states (new/stable/intermittent/offline) |
| `/api/node-states/summary` | GET | Node state summary (counts by state) |
| `/api/config-drift` | GET | Configuration drift events (firmware/hardware changes) |
| `/api/mqtt/stats` | GET | MQTT subscriber statistics |

### Configuration

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/config` | GET | Current configuration |
| `/api/tile-providers` | GET | Available tile layers |
| `/api/sources` | GET | Enabled data sources |
| `/api/core-health` | GET | Cross-process health state (shared memory) |
| `/api/proxy/stats` | GET | Meshtastic API proxy statistics |

## Offline Tile Caching

The service worker (`sw-tiles.js`) provides offline map tile access:

```mermaid
flowchart LR
    REQ["Tile Request"] --> SW["Service Worker"]
    SW -->|cache hit| CACHE["CacheStorage<br/>meshforge-maps-tiles-v1"]
    SW -->|cache miss| NET["Network Fetch"]
    NET -->|store| CACHE
    CACHE -->|LRU eviction<br/>at 2000 tiles| EVICT["Remove oldest"]
    CACHE --> RESP["Response to Map"]
```

- **Tiles:** Cache-first strategy (instant offline response)
- **API:** Network-first with cache fallback
- **CDN assets:** Cache-first (Leaflet, D3, MarkerCluster)
- **Max cache:** 2000 tiles with LRU eviction

## Tile Providers

| Key | Name | Best For |
|-----|------|----------|
| `carto_dark` | CartoDB Dark Matter | NOC / night operations |
| `osm_standard` | OpenStreetMap | General reference |
| `osm_topo` | OpenTopoMap | Terrain / elevation planning |
| `esri_satellite` | Esri Satellite | RF line-of-sight / terrain |
| `esri_topo` | Esri Topographic | Field operations |
| `stadia_terrain` | Stadia Terrain | Landscape overview |

## Testing

```bash
pip install pytest
pytest tests/ -v
# 835 tests covering:
#   - Base helpers, config, coordinate validation
#   - All 4 collectors (Meshtastic, Reticulum, HamClock, AREDN)
#   - Aggregator deduplication, MQTT node store, topology links
#   - Map server startup/port fallback, plugin lifecycle/events
#   - Circuit breaker, reconnect strategy, event bus
#   - WebSocket server, real-time pipeline
#   - OpenHamClock auto-detection and port priority
#   - Per-node health scoring (all 5 components, normalization, cache)
#   - Performance profiling (timing, percentiles, memory)
#   - AREDN hardening (network errors, malformed responses, cache, LQM edges)
#   - Node history DB, shared health state, topology GeoJSON
#   - Config drift detection, node state machine
#   - Alert engine (rules, cooldown, MQTT publish, EventBus propagation, webhooks)
#   - Historical analytics (growth, heatmap, ranking, summary, alert trends)
```

## Roadmap

### Near-term

- ~~**Analytics frontend**~~ (done) -- browser-based analytics dashboard with SVG sparkline charts, activity heatmap, node ranking table, and alert trend visualization. Toggle via Overlays > Analytics.

- **Email alert delivery** -- SMTP integration for the alert engine. The delivery pipeline already supports multiple channels (callback, MQTT, webhook); email is a natural extension.

- ~~**CSV/JSON export**~~ (done) -- export endpoints at `/api/export/nodes`, `/api/export/alerts`, and `/api/export/analytics/*` serving CSV downloads with JSON option. Also available via analytics panel export buttons.

### Medium-term

- **Multi-instance federation** -- peer multiple MeshForge Maps instances across geographies for distributed NOC views. Lightweight gossip protocol over MQTT or HTTP for node and health state synchronization. Aggregate topology view spanning federated instances.

- **Mobile / PWA** -- Progressive Web App with push notifications for critical health alerts, offline-first data access, responsive touch UI optimized for field operations on tablets and phones. Service worker already provides tile caching foundation.

### Ongoing

- Collector hardening and edge case coverage across all data sources
- Performance optimization for large meshes (1000+ nodes)
- Community-contributed tile providers and overlay plugins

## Contributing

Follow [MeshForge contributing guidelines](https://github.com/Nursedude/meshforge/blob/main/CONTRIBUTING.md):

- Python 3.9+, PEP 8, type hints encouraged
- Commit convention: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`
- No `shell=True` in subprocess, no bare `except:`, no `os.system()`
- Validate all user inputs, HTML-escape all output
- Network bindings default to `127.0.0.1`
- PR with summary, changes list, and testing checklist

## License

[GPL-3.0](LICENSE) -- same as MeshForge core.

## Related Projects

- [MeshForge](https://github.com/Nursedude/meshforge) -- Turnkey Mesh Network Operations Center
- [OpenHamClock](https://github.com/accius/openhamclock) -- Ham radio propagation dashboard (recommended)
- [RMAP.world](https://rmap.world) -- Reticulum Network World Map
- [Reticulum Community Hub](https://github.com/FreeTAKTeam/Reticulum-Telemetry-Hub) -- RCH FastAPI telemetry hub
- [meshtastic-map](https://github.com/liamcottle/meshtastic-map) -- Meshtastic MQTT map (reference implementation)
- [AREDN World Map](https://worldmap.arednmesh.org/) -- Global AREDN node visualization
- [AREDN](https://www.arednmesh.org/) -- Amateur Radio Emergency Data Network
- [Meshtastic](https://meshtastic.org/) -- LoRa mesh networking platform
- [Reticulum](https://reticulum.network/) -- Cryptographic mesh networking stack
