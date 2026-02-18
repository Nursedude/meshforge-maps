// =========================================================================
// MeshForge Maps - Leaflet Frontend
// =========================================================================

const API_BASE = window.location.origin;

// Fetch with retry (exponential backoff) for API resilience
async function fetchWithRetry(url, retries, baseDelay) {
    retries = retries || 2;
    baseDelay = baseDelay || 1000;
    for (let attempt = 0; attempt <= retries; attempt++) {
        try {
            const resp = await fetch(url);
            if (resp.ok) return resp;
            if (resp.status >= 500 && attempt < retries) {
                await new Promise(r => setTimeout(r, baseDelay * Math.pow(2, attempt)));
                continue;
            }
            return resp; // Return non-retryable errors as-is
        } catch (e) {
            if (attempt >= retries) throw e;
            await new Promise(r => setTimeout(r, baseDelay * Math.pow(2, attempt)));
        }
    }
}

// Network colors (matches meshforge core palette)
const NETWORK_COLORS = {
    meshtastic: '#66bb6a',
    reticulum: '#ab47bc',
    aredn:     '#ff7043',
    hamclock:  '#42a5f5',
};

// Tile provider definitions (loaded from server, with fallback)
let TILE_PROVIDERS = {
    carto_dark: {
        name: 'CartoDB Dark Matter',
        url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        attribution: '&copy; OSM &copy; CARTO',
        max_zoom: '20',
    },
    osm_standard: {
        name: 'OpenStreetMap',
        url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        attribution: '&copy; OpenStreetMap contributors',
        max_zoom: '19',
    },
    osm_topo: {
        name: 'OpenTopoMap',
        url: 'https://tile.opentopomap.org/{z}/{x}/{y}.png',
        attribution: '&copy; OpenTopoMap (CC-BY-SA)',
        max_zoom: '17',
    },
    esri_satellite: {
        name: 'Esri Satellite',
        url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attribution: '&copy; Esri, Maxar, Earthstar',
        max_zoom: '19',
    },
    esri_topo: {
        name: 'Esri Topographic',
        url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
        attribution: '&copy; Esri, HERE, Garmin',
        max_zoom: '19',
    },
    stadia_terrain: {
        name: 'Stadia Terrain',
        url: 'https://tiles.stadiamaps.com/tiles/stamen_terrain/{z}/{x}/{y}.png',
        attribution: '&copy; Stadia Maps &copy; Stamen Design',
        max_zoom: '18',
    },
};

// =========================================================================
// State
// =========================================================================

let map;
let currentTileLayer;
let clusterGroup;
let directGroup;        // non-clustered layer group
let terminatorLayer;
let topologyLayer;      // Leaflet GeoJSON layer for topology links
let showTopology = false;
let useClustering = true;
let isLoading = false;
let lastOverlayData = null;  // cached overlay from last successful fetch
let consecutiveErrors = 0;
let ws = null;               // WebSocket connection (real-time updates)
let wsReconnectTimer = null; // Timer for WebSocket reconnect attempts
let wsReconnectDelay = 2000; // Current reconnect delay (exponential backoff)
let trajectoryLayers = {};   // Active trajectory polylines keyed by node_id
const MAX_TRAJECTORIES = 20; // Cap to prevent unbounded memory growth
let historyPanelOpen = false;
let healthOverlayActive = false;  // Whether health color-coding is enabled
let nodeHealthScores = {};        // Cached {node_id: {score, status}} from API
const markerRegistry = new Map(); // nodeId -> marker for O(1) WebSocket lookup
let alertPanelOpen = false;       // Whether alert panel is visible
const alertItems = [];            // In-memory alert buffer (newest first)
const MAX_ALERT_ITEMS = 100;      // Cap alert panel entries

// Per-network layer groups (for toggle visibility)
const networkLayers = {
    meshtastic: [],
    reticulum: [],
    aredn: [],
};
const networkVisible = {
    meshtastic: true,
    reticulum: true,
    aredn: true,
};
let allFeatures = [];

// =========================================================================
// Initialization
// =========================================================================

function initMap() {
    map = L.map('map', {
        center: [20, -100],
        zoom: 4,
        zoomControl: true,
        attributionControl: true,
    });

    // Default tile layer
    currentTileLayer = L.tileLayer(TILE_PROVIDERS.carto_dark.url, {
        attribution: TILE_PROVIDERS.carto_dark.attribution,
        maxZoom: parseInt(TILE_PROVIDERS.carto_dark.max_zoom),
    }).addTo(map);

    // Cluster group
    clusterGroup = L.markerClusterGroup({
        maxClusterRadius: 50,
        spiderfyOnMaxZoom: true,
        showCoverageOnHover: false,
        zoomToBoundsOnClick: true,
        iconCreateFunction: function(cluster) {
            const count = cluster.getChildCount();
            let size = 'small';
            if (count > 50) size = 'large';
            else if (count > 10) size = 'medium';
            return L.divIcon({
                html: '<div>' + count + '</div>',
                className: 'marker-cluster marker-cluster-' + size,
                iconSize: L.point(40, 40),
            });
        },
    }).addTo(map);

    directGroup = L.layerGroup();

    // Populate tile selector
    populateTileSelector();

    // Load configuration from server
    loadConfig();

    // Load node data
    loadNodeData();

    // Load initial active alerts from API
    loadInitialAlerts();

    // Shift map down for header
    map.getContainer().style.marginTop = '48px';
    map.getContainer().style.height = 'calc(100vh - 48px)';
    map.invalidateSize();
}

async function loadConfig() {
    try {
        const resp = await fetch(API_BASE + '/api/tile-providers');
        if (resp.ok) {
            const providers = await resp.json();
            if (Object.keys(providers).length > 0) {
                TILE_PROVIDERS = providers;
                populateTileSelector();
            }
        }
    } catch (e) {
        console.debug('Using built-in tile providers');
    }

    try {
        const resp = await fetch(API_BASE + '/api/config');
        if (resp.ok) {
            const config = await resp.json();
            if (config.map_center_lat && config.map_center_lon) {
                map.setView(
                    [config.map_center_lat, config.map_center_lon],
                    config.map_default_zoom || 4
                );
            }
            if (config.default_tile_provider && TILE_PROVIDERS[config.default_tile_provider]) {
                document.getElementById('tileSelect').value = config.default_tile_provider;
                changeTileLayer();
            }
            // Connect WebSocket for real-time updates
            if (config.ws_port) {
                connectWebSocket(config.ws_port);
            }
            // Fetch version from status endpoint (upstream: version badge)
            fetchVersion();
        }
    } catch (e) {
        console.debug('Using default config');
    }
}

async function fetchVersion() {
    try {
        const resp = await fetch(API_BASE + '/api/status');
        if (resp.ok) {
            const status = await resp.json();
            if (status.version) {
                var sub = document.getElementById('headerSubtitle');
                if (sub) sub.textContent = 'v' + status.version;
            }
            // Show MQTT live node count in header (upstream: monitoring)
            if (status.mqtt_live === 'connected' && status.mqtt_node_count > 0) {
                var badge = document.getElementById('mqttBadge');
                var count = document.getElementById('statMqtt');
                if (badge) badge.style.display = '';
                if (count) count.textContent = status.mqtt_node_count;
            }
        }
    } catch (e) {
        // Non-critical, silently ignore
    }
}

function populateTileSelector() {
    const select = document.getElementById('tileSelect');
    select.innerHTML = '';
    for (const [key, provider] of Object.entries(TILE_PROVIDERS)) {
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = provider.name;
        select.appendChild(opt);
    }
}

// =========================================================================
// Connection Status & Notifications
// =========================================================================

function setConnectionStatus(status, label) {
    const el = document.getElementById('connStatus');
    const labelEl = document.getElementById('connLabel');
    el.className = 'status-indicator status-' + status;
    labelEl.textContent = label || status;
}

function showToast(message, isError) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = 'toast' + (isError ? ' toast-error' : '') + ' visible';
    clearTimeout(toast._timer);
    toast._timer = setTimeout(function() {
        toast.className = 'toast';
    }, 3000);
}

function updateLastUpdated() {
    const el = document.getElementById('lastUpdated');
    if (el) {
        el.textContent = 'Updated: ' + new Date().toLocaleTimeString();
    }
}

// =========================================================================
// Data Loading
// =========================================================================

async function loadNodeData() {
    if (isLoading) return;  // prevent concurrent fetches
    isLoading = true;
    setConnectionStatus('loading', 'Loading');
    const btn = document.getElementById('refreshBtn');
    if (btn) btn.disabled = true;

    try {
        const resp = await fetchWithRetry(API_BASE + '/api/nodes/geojson', 2, 1000);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        processGeoJSON(data);
        setConnectionStatus('connected', 'Connected');
        updateLastUpdated();
        consecutiveErrors = 0;
        // Load HamClock propagation data (non-blocking)
        loadHamClockData();
    } catch (e) {
        consecutiveErrors++;
        console.error('Failed to load node data:', e);
        setConnectionStatus('error', 'Error');
        if (consecutiveErrors === 1) {
            showToast('Failed to load node data: ' + e.message, true);
        } else if (consecutiveErrors === 3) {
            showToast('Server unreachable after 3 attempts', true);
        }
    } finally {
        isLoading = false;
        if (btn) btn.disabled = false;
    }
}

function renderMarkers() {
    // Core marker rendering from allFeatures — single source of truth
    // for both initial load (processGeoJSON) and re-render (health overlay toggle)
    clusterGroup.clearLayers();
    directGroup.clearLayers();
    markerRegistry.clear();

    const counts = { meshtastic: 0, reticulum: 0, aredn: 0 };
    networkLayers.meshtastic = [];
    networkLayers.reticulum = [];
    networkLayers.aredn = [];

    for (const feature of allFeatures) {
        const props = feature.properties || {};
        const coords = feature.geometry?.coordinates;
        if (!coords || coords.length < 2) continue;

        const lat = coords[1];
        const lon = coords[0];
        const network = props.network || 'unknown';

        // Determine color: health overlay or network color
        let color;
        if (healthOverlayActive) {
            color = getHealthColor(props.id) || '#78909c';
        } else {
            color = NETWORK_COLORS[network] || '#78909c';
        }

        const isStale = props.is_online === false;
        const marker = L.circleMarker([lat, lon], {
            radius: isStale ? 5 : 7,
            fillColor: color,
            color: isStale ? '#546e7a' : color,
            weight: isStale ? 1 : 2,
            opacity: isStale ? 0.4 : 1.0,
            fillOpacity: isStale ? 0.2 : 0.7,
        });

        marker.bindPopup(buildPopup(props, color));

        if (props.id) {
            markerRegistry.set(props.id, marker);
        }

        if (counts.hasOwnProperty(network)) {
            counts[network]++;
            networkLayers[network].push(marker);
        }

        if (networkVisible[network] !== false) {
            if (useClustering) {
                clusterGroup.addLayer(marker);
            } else {
                directGroup.addLayer(marker);
            }
        }
    }

    document.getElementById('countMeshtastic').textContent = counts.meshtastic;
    document.getElementById('countReticulum').textContent = counts.reticulum;
    document.getElementById('countAredn').textContent = counts.aredn;
    document.getElementById('statMeshtastic').textContent = counts.meshtastic;
    document.getElementById('statReticulum').textContent = counts.reticulum;
    document.getElementById('statAredn').textContent = counts.aredn;
}

function processGeoJSON(data) {
    allFeatures = data.features || [];
    renderMarkers();

    // Process overlay data (space weather, terminator)
    const overlayData = data.properties?.overlay_data || {};
    if (overlayData && Object.keys(overlayData).length > 0) {
        lastOverlayData = overlayData;
    }
    updateSpaceWeather(overlayData.space_weather);
    if (document.getElementById('overlayTerminator').checked) {
        updateTerminator(overlayData.solar_terminator);
    }
}

function buildPopup(props, color) {
    const rows = [];

    // Helper: only add row if value is defined and non-empty
    function addRow(label, value, suffix) {
        if (value != null && value !== '') {
            const display = suffix ? esc(String(value)) + suffix : esc(String(value));
            rows.push(`<div class="popup-row"><span class="popup-key">${esc(label)}</span><span class="popup-val">${display}</span></div>`);
        }
    }

    addRow('Type', props.node_type, '');
    addRow('Hardware', props.hardware, '');
    addRow('Role', props.role, '');

    // Relay indicator
    if (props.is_relay) {
        rows.push(`<div class="popup-row"><span class="popup-key">Relay</span><span class="popup-val" style="color:#ffa726">Yes</span></div>`);
    }
    if (props.via_mqtt) {
        rows.push(`<div class="popup-row"><span class="popup-key">Via</span><span class="popup-val" style="color:#42a5f5">MQTT</span></div>`);
    }

    if (props.battery != null) addRow('Battery', props.battery, '%');
    if (props.voltage != null) addRow('Voltage', Number(props.voltage).toFixed(2), 'V');
    if (props.snr != null) addRow('SNR', props.snr, ' dB');
    if (props.rssi != null) addRow('RSSI', props.rssi, ' dBm');
    if (props.altitude != null) addRow('Altitude', props.altitude, 'm');
    if (props.hops_away != null) addRow('Hops', props.hops_away, '');

    // Channel utilization warning (upstream improvement)
    if (props.channel_util != null) {
        const cu = Number(props.channel_util);
        const cuColor = cu > 50 ? '#ef5350' : cu > 25 ? '#ffa726' : '#66bb6a';
        rows.push(`<div class="popup-row"><span class="popup-key">Ch Util</span><span class="popup-val" style="color:${cuColor}">${cu.toFixed(1)}%</span></div>`);
    }
    if (props.air_util_tx != null) {
        addRow('Air TX', Number(props.air_util_tx).toFixed(1), '%');
    }

    // Environmental sensors
    if (props.temperature != null) addRow('Temp', Number(props.temperature).toFixed(1), '\u00b0C');
    if (props.humidity != null) addRow('Humidity', Number(props.humidity).toFixed(0), '%');
    if (props.pressure != null) addRow('Pressure', Number(props.pressure).toFixed(1), ' hPa');
    addRow('Firmware', props.firmware, '');

    // AREDN link type indicator with color coding
    if (props.link_type) {
        const ltColor = props.link_type === 'RF' ? '#ff7043' :
                        props.link_type === 'DTD' ? '#66bb6a' :
                        props.link_type === 'TUN' ? '#42a5f5' :
                        props.link_type === 'XLINK' ? '#ab47bc' : '#78909c';
        rows.push(`<div class="popup-row"><span class="popup-key">Link</span><span class="popup-val" style="color:${ltColor}">${esc(props.link_type)}</span></div>`);
    }
    addRow('Info', props.description, '');

    const onlineStr = props.is_online === true ? 'Online' :
                      props.is_online === false ? 'Offline' : 'Unknown';
    const onlineColor = props.is_online === true ? '#66bb6a' :
                        props.is_online === false ? '#ef5350' : '#78909c';

    // Last-seen timestamp
    let lastSeenStr = '';
    if (props.last_seen) {
        try {
            const ts = typeof props.last_seen === 'number' ? new Date(props.last_seen * 1000) : new Date(props.last_seen);
            if (!isNaN(ts.getTime())) {
                lastSeenStr = `<div class="popup-row" style="font-size:10px;color:#546e7a"><span class="popup-key">Seen</span><span class="popup-val">${ts.toLocaleString()}</span></div>`;
            }
        } catch (e) { /* ignore parse errors */ }
    }

    // Health score badge (if available)
    let healthBadge = '';
    const hs = nodeHealthScores[props.id];
    if (hs) {
        healthBadge = `<div class="popup-row"><span class="popup-key">Health</span><span class="popup-val"><span class="health-badge health-${hs.status}">${hs.score} ${hs.status}</span></span></div>`;
    }

    return `
        <div class="popup-title" style="color:${color}">${esc(props.name || props.id || 'Unknown')}</div>
        <div class="popup-network" style="color:${color}">${esc(props.network || '')} <span style="color:${onlineColor}">${onlineStr}</span></div>
        ${healthBadge}
        ${rows.join('')}
        ${lastSeenStr}
        <div class="popup-row" style="margin-top:4px;font-size:10px;color:#546e7a">
            <span>ID: ${esc(props.id || '')}</span>
        </div>
    `;
}

function esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

// =========================================================================
// UI Controls
// =========================================================================

function changeTileLayer() {
    const key = document.getElementById('tileSelect').value;
    const provider = TILE_PROVIDERS[key];
    if (!provider) return;

    if (currentTileLayer) {
        map.removeLayer(currentTileLayer);
    }
    currentTileLayer = L.tileLayer(provider.url, {
        attribution: provider.attribution,
        maxZoom: parseInt(provider.max_zoom) || 19,
    }).addTo(map);
}

function toggleLayer(network) {
    const checkbox = document.getElementById('layer' + network.charAt(0).toUpperCase() + network.slice(1));
    networkVisible[network] = checkbox.checked;
    rebuildMarkers();
}

function toggleClustering() {
    useClustering = document.getElementById('overlayClustering').checked;
    rebuildMarkers();
}

function rebuildMarkers() {
    clusterGroup.clearLayers();
    directGroup.clearLayers();

    if (useClustering) {
        if (map.hasLayer(directGroup)) map.removeLayer(directGroup);
        if (!map.hasLayer(clusterGroup)) map.addLayer(clusterGroup);
    } else {
        if (map.hasLayer(clusterGroup)) map.removeLayer(clusterGroup);
        if (!map.hasLayer(directGroup)) map.addLayer(directGroup);
    }

    for (const network of Object.keys(networkLayers)) {
        if (!networkVisible[network]) continue;
        for (const marker of networkLayers[network]) {
            if (useClustering) {
                clusterGroup.addLayer(marker);
            } else {
                directGroup.addLayer(marker);
            }
        }
    }
}

async function toggleTerminator() {
    const show = document.getElementById('overlayTerminator').checked;
    if (show) {
        // Use cached overlay or fetch from lightweight overlay endpoint
        if (lastOverlayData && lastOverlayData.solar_terminator) {
            updateTerminator(lastOverlayData.solar_terminator);
        } else {
            try {
                const resp = await fetch(API_BASE + '/api/overlay');
                if (resp.ok) {
                    const overlay = await resp.json();
                    lastOverlayData = overlay;
                    updateTerminator(overlay.solar_terminator);
                }
            } catch (e) {
                console.debug('Overlay fetch failed:', e);
            }
        }
    } else if (terminatorLayer) {
        map.removeLayer(terminatorLayer);
        terminatorLayer = null;
    }
}

function togglePanel() {
    const panel = document.getElementById('controlPanel');
    panel.classList.toggle('visible');
}

async function refreshData() {
    showToast('Refreshing all sources...');
    await loadNodeData();
    if (showTopology) await loadTopologyData();
    if (consecutiveErrors === 0) {
        showToast('Data refreshed');
    }
}

// =========================================================================
// Space Weather Display
// =========================================================================

function updateSpaceWeather(weather) {
    if (!weather) return;

    const sfiEl = document.getElementById('wxSFI');
    const kpEl = document.getElementById('wxKp');
    const windEl = document.getElementById('wxWind');
    const bandEl = document.getElementById('wxBand');
    const bandBar = document.getElementById('bandBar');

    if (weather.solar_flux) sfiEl.textContent = weather.solar_flux;
    if (weather.kp_index != null) kpEl.textContent = Number(weather.kp_index).toFixed(1);
    if (weather.solar_wind_speed) windEl.textContent = weather.solar_wind_speed;

    const cond = weather.band_conditions || 'unknown';
    bandEl.textContent = cond.charAt(0).toUpperCase() + cond.slice(1);

    bandBar.className = 'band-conditions band-' + cond;
    bandBar.textContent = 'Band Conditions: ' + cond.charAt(0).toUpperCase() + cond.slice(1);
}

// =========================================================================
// HamClock Propagation Panel
// =========================================================================

async function loadHamClockData() {
    try {
        var resp = await fetch(API_BASE + '/api/hamclock');
        if (!resp.ok) {
            document.getElementById('hamclockSection').style.display = 'none';
            return;
        }
        var data = await resp.json();
        updateHamClockPanel(data);
    } catch (e) {
        console.debug('HamClock data unavailable:', e);
        document.getElementById('hamclockSection').style.display = 'none';
    }
}

function updateHamClockPanel(data) {
    var section = document.getElementById('hamclockSection');
    if (!data) { section.style.display = 'none'; return; }
    section.style.display = '';

    // Source indicator
    var srcEl = document.getElementById('hamclockSource');
    if (data.available) {
        var srcName = (data.source && data.source.indexOf('OpenHamClock') >= 0) ? 'OpenHamClock' : 'HamClock';
        srcEl.textContent = 'Source: ' + srcName + ' API (' + esc(data.host) + ':' + data.port + ')';
        srcEl.className = 'hamclock-source hamclock-source-active';
    } else {
        srcEl.textContent = 'Source: NOAA SWPC (OpenHamClock unavailable)';
        srcEl.className = 'hamclock-source hamclock-source-fallback';
    }

    // DE/DX Station info
    var stationEl = document.getElementById('stationInfo');
    if (data.de_station || data.dx_station) {
        stationEl.style.display = '';
        if (data.de_station) {
            document.getElementById('deCall').textContent = data.de_station.call || '--';
            document.getElementById('deGrid').textContent = data.de_station.grid || '--';
        }
        if (data.dx_station) {
            document.getElementById('dxCall').textContent = data.dx_station.call || '--';
            document.getElementById('dxGrid').textContent = data.dx_station.grid || '--';
        }
    } else {
        stationEl.style.display = 'none';
    }

    // VOACAP bands
    var voacapEl = document.getElementById('voacapBands');
    if (data.voacap && data.voacap.bands && Object.keys(data.voacap.bands).length > 0) {
        var html = '';
        var bandOrder = ['80m', '40m', '30m', '20m', '17m', '15m', '12m', '10m'];
        var bands = data.voacap.bands;
        for (var i = 0; i < bandOrder.length; i++) {
            var band = bandOrder[i];
            if (!bands[band]) continue;
            var info = bands[band];
            var rel = info.reliability || 0;
            var status = info.status || 'closed';
            var snrStr = (info.snr != null) ? info.snr + 'dB' : '';
            html += '<div class="voacap-band-row">' +
                '<span class="voacap-band-label">' + esc(band) + '</span>' +
                '<div class="voacap-bar-bg"><div class="voacap-bar-fill voacap-fill-' + status + '" style="width:' + rel + '%"></div></div>' +
                '<span class="voacap-bar-text">' + rel + '%</span>' +
                '<span class="voacap-snr-text">' + snrStr + '</span>' +
                '</div>';
        }
        if (data.voacap.best_band) {
            html += '<div style="font-size:10px;color:#42a5f5;text-align:center;margin-top:4px">Best: ' + esc(data.voacap.best_band) + ' (' + (data.voacap.best_reliability || 0) + '%)</div>';
        }
        voacapEl.innerHTML = html;
    } else if (data.band_conditions && data.band_conditions.bands) {
        var html = '';
        var bc = data.band_conditions.bands;
        for (var key in bc) {
            html += '<div class="voacap-band-row">' +
                '<span class="voacap-band-label">' + esc(key) + '</span>' +
                '<span style="font-size:11px;color:#90a4ae">' + esc(bc[key]) + '</span>' +
                '</div>';
        }
        voacapEl.innerHTML = html;
    } else {
        voacapEl.innerHTML = '';
    }

    // DX Spots
    var dxContainer = document.getElementById('dxspotContainer');
    var dxList = document.getElementById('dxspotList');
    if (data.dxspots && data.dxspots.length > 0) {
        dxContainer.style.display = '';
        var html = '';
        var spots = data.dxspots.slice(0, 10); // Show latest 10
        for (var i = 0; i < spots.length; i++) {
            var s = spots[i];
            html += '<div class="dxspot-row">' +
                '<span class="dxspot-call">' + esc(s.dx_call) + '</span>' +
                '<span class="dxspot-freq">' + esc(s.freq_khz) + '</span>' +
                '<span class="dxspot-de">' + esc(s.de_call || '') + '</span>' +
                '<span class="dxspot-time">' + esc(s.utc || '') + '</span>' +
                '</div>';
        }
        dxList.innerHTML = html;
    } else {
        dxContainer.style.display = 'none';
    }
}

// =========================================================================
// Solar Terminator
// =========================================================================

function updateTerminator(termData) {
    if (!termData) return;
    if (terminatorLayer) {
        map.removeLayer(terminatorLayer);
    }

    // Calculate terminator polygon from subsolar point using proper
    // spherical geometry that accounts for solar declination year-round.
    const subLat = termData.subsolar_lat;  // solar declination
    const subLon = termData.subsolar_lon;
    const points = [];

    const decRad = subLat * Math.PI / 180;

    // For each longitude, compute the latitude where the solar zenith
    // angle equals 90 degrees (i.e., the sun is exactly on the horizon).
    // Formula: cos(zen) = sin(lat)*sin(dec) + cos(lat)*cos(dec)*cos(HA)
    // At zen=90: 0 = sin(lat)*sin(dec) + cos(lat)*cos(dec)*cos(HA)
    // Solving for lat: lat = atan(-cos(HA) * cos(dec) / sin(dec))
    //   simplified:    lat = atan(-cos(HA) / tan(dec))
    // This is the standard terminator formula valid for all seasons.
    for (let lng = -180; lng <= 180; lng += 2) {
        const ha = (lng - subLon) * Math.PI / 180;  // hour angle

        // Guard against near-equinox singularity (dec ~0)
        let terminatorLat;
        if (Math.abs(decRad) < 0.001) {
            // At equinox the terminator is a great circle through the poles
            terminatorLat = -Math.cos(ha) > 0 ? 90 : -90;
            // Smooth to avoid discontinuity
            terminatorLat = Math.atan2(-Math.cos(ha), 0.001) * 180 / Math.PI;
        } else {
            terminatorLat = Math.atan(-Math.cos(ha) / Math.tan(decRad)) * 180 / Math.PI;
        }

        // Clamp to valid lat range (handles polar day/night edge cases)
        terminatorLat = Math.max(-89.99, Math.min(89.99, terminatorLat));
        points.push([terminatorLat, lng]);
    }

    // Determine which pole is dark (opposite of subsolar latitude)
    const darkPole = subLat > 0 ? -90 : 90;
    const nightPoly = [];

    // Top/bottom edge
    nightPoly.push([darkPole, -180]);
    for (const p of points) {
        nightPoly.push(p);
    }
    nightPoly.push([darkPole, 180]);
    nightPoly.push([darkPole, -180]);

    terminatorLayer = L.polygon(nightPoly, {
        color: 'rgba(255, 193, 7, 0.4)',
        fillColor: 'rgba(0, 0, 0, 0.3)',
        fillOpacity: 0.3,
        weight: 1.5,
        interactive: false,
    }).addTo(map);
}

// =========================================================================
// Node Health Overlay
// =========================================================================

const HEALTH_COLORS = {
    excellent: '#66bb6a',
    good:      '#81c784',
    fair:      '#ffa726',
    poor:      '#ef5350',
    critical:  '#e53935',
    unknown:   '#78909c',
};

async function toggleHealthOverlay() {
    healthOverlayActive = document.getElementById('overlayHealth').checked;
    if (healthOverlayActive) {
        await loadNodeHealthData();
    }
    // Re-render markers with health coloring
    rebuildMarkersFromFeatures();
}

async function loadNodeHealthData() {
    try {
        var resp = await fetch(API_BASE + '/api/node-health');
        if (!resp.ok) return;
        var data = await resp.json();
        nodeHealthScores = {};
        if (data.nodes) {
            for (var i = 0; i < data.nodes.length; i++) {
                var n = data.nodes[i];
                nodeHealthScores[n.node_id] = { score: n.score, status: n.status };
            }
        }
    } catch (e) {
        console.debug('Node health data unavailable:', e);
    }
}

function getHealthColor(nodeId) {
    var hs = nodeHealthScores[nodeId];
    if (!hs) return null;
    return HEALTH_COLORS[hs.status] || HEALTH_COLORS.unknown;
}

function rebuildMarkersFromFeatures() {
    renderMarkers();
}

// =========================================================================
// Topology / Link Visualization (GeoJSON from server)
// =========================================================================

function toggleTopology() {
    showTopology = document.getElementById('overlayTopology').checked;
    if (showTopology) {
        loadTopologyData();
    } else if (topologyLayer) {
        map.removeLayer(topologyLayer);
        topologyLayer = null;
        document.getElementById('countLinks').textContent = '0';
    }
}

async function loadTopologyData() {
    try {
        const resp = await fetch(API_BASE + '/api/topology/geojson');
        if (!resp.ok) return;
        const geojson = await resp.json();
        renderTopologyGeoJSON(geojson);
    } catch (e) {
        console.debug('Topology data unavailable:', e);
    }
}

function renderTopologyGeoJSON(geojson) {
    if (topologyLayer) {
        map.removeLayer(topologyLayer);
    }

    topologyLayer = L.geoJSON(geojson, {
        style: function(feature) {
            var props = feature.properties || {};
            var color = props.color || '#6b7280';
            var quality = props.quality || 'Unknown';
            var snr = props.snr;
            var weight = 1.5;
            var opacity = 0.5;

            if (quality === 'Excellent')     { weight = 2.5; opacity = 0.8; }
            else if (quality === 'Good')     { weight = 2.2; opacity = 0.75; }
            else if (quality === 'Marginal') { weight = 2.0; opacity = 0.7; }
            else if (quality === 'Poor')     { weight = 1.8; opacity = 0.65; }
            else if (quality === 'Bad')      { weight = 1.5; opacity = 0.6; }

            return {
                color: color,
                weight: weight,
                opacity: opacity,
                dashArray: (snr != null && snr > 0) ? null : '4 6',
                interactive: true,
            };
        },
        onEachFeature: function(feature, layer) {
            var props = feature.properties || {};
            var color = props.color || '#6b7280';
            var snr = props.snr;
            var snrText = (snr != null) ? Number(snr).toFixed(1) + ' dB' : 'unknown';
            var quality = props.quality || 'Unknown';
            var network = props.network || '';
            var linkType = props.link_type || '';

            var popup = '<div class="popup-title" style="color:' + color + '">Mesh Link</div>' +
                '<div class="popup-row"><span class="popup-key">From</span><span class="popup-val">' + esc(props.source || '') + '</span></div>' +
                '<div class="popup-row"><span class="popup-key">To</span><span class="popup-val">' + esc(props.target || '') + '</span></div>' +
                '<div class="popup-row"><span class="popup-key">SNR</span><span class="popup-val">' + snrText + '</span></div>' +
                '<div class="popup-row"><span class="popup-key">Quality</span><span class="popup-val" style="color:' + color + '">' + esc(quality) + '</span></div>';
            if (network) {
                popup += '<div class="popup-row"><span class="popup-key">Network</span><span class="popup-val">' + esc(network) + '</span></div>';
            }
            if (linkType) {
                popup += '<div class="popup-row"><span class="popup-key">Type</span><span class="popup-val">' + esc(linkType) + '</span></div>';
            }
            layer.bindPopup(popup);
        },
    }).addTo(map);

    var linkCount = (geojson.features || []).length;
    document.getElementById('countLinks').textContent = linkCount;
}

// =========================================================================
// Node History & Trajectory Visualization
// =========================================================================

function toggleTrajectoryPanel() {
    historyPanelOpen = !historyPanelOpen;
    var panel = document.getElementById('historyPanel');
    var checkbox = document.getElementById('overlayTrajectory');
    if (historyPanelOpen) {
        panel.style.display = '';
        checkbox.checked = true;
        loadTrackedNodes();
    } else {
        panel.style.display = 'none';
        checkbox.checked = false;
        clearAllTrajectories();
    }
}

async function loadTrackedNodes() {
    var listEl = document.getElementById('historyNodeList');
    try {
        var resp = await fetch(API_BASE + '/api/history/nodes');
        if (!resp.ok) {
            listEl.innerHTML = '<div style="color:#ef5350;font-size:11px;text-align:center;padding:12px">Node history unavailable</div>';
            return;
        }
        var data = await resp.json();
        var nodes = data.nodes || [];
        if (nodes.length === 0) {
            listEl.innerHTML = '<div style="color:#546e7a;font-size:11px;text-align:center;padding:12px">No tracked nodes yet</div>';
            return;
        }
        var html = '';
        for (var i = 0; i < nodes.length; i++) {
            var n = nodes[i];
            var nodeId = n.node_id || n.id || '';
            var count = n.observation_count || n.count || 0;
            var isActive = trajectoryLayers[nodeId] ? ' active' : '';
            html += '<div class="history-node-row" data-node-id="' + esc(nodeId) + '" onclick="toggleTrajectory(this.dataset.nodeId)">' +
                '<span class="history-node-id">' + esc(nodeId) + '</span>' +
                '<span class="history-node-count">' + count + ' pts</span>' +
                '<button class="trajectory-btn' + isActive + '" data-node="' + esc(nodeId) + '">' +
                (isActive ? 'Hide' : 'Show') + '</button>' +
                '</div>';
        }
        listEl.innerHTML = html;
    } catch (e) {
        listEl.innerHTML = '<div style="color:#ef5350;font-size:11px;text-align:center;padding:12px">Failed to load history</div>';
    }
}

async function toggleTrajectory(nodeId) {
    if (trajectoryLayers[nodeId]) {
        // Remove existing trajectory
        map.removeLayer(trajectoryLayers[nodeId]);
        delete trajectoryLayers[nodeId];
        loadTrackedNodes(); // refresh button states
        return;
    }

    try {
        var resp = await fetch(API_BASE + '/api/nodes/' + encodeURIComponent(nodeId) + '/trajectory');
        if (!resp.ok) {
            showToast('No trajectory data for ' + nodeId, true);
            return;
        }
        var geojson = await resp.json();

        if (!geojson.geometry || !geojson.geometry.coordinates || geojson.geometry.coordinates.length < 2) {
            showToast('Not enough points for trajectory', true);
            return;
        }

        // Render trajectory as a Leaflet polyline with gradient
        var coords = geojson.geometry.coordinates;
        var latlngs = coords.map(function(c) { return [c[1], c[0]]; });

        var trajectoryLine = L.polyline(latlngs, {
            color: '#ffc107',
            weight: 3,
            opacity: 0.8,
            dashArray: '6 4',
            interactive: true,
        });

        // Add start/end markers
        var startMarker = L.circleMarker(latlngs[0], {
            radius: 5, fillColor: '#66bb6a', color: '#fff', weight: 1, fillOpacity: 0.9,
        });
        startMarker.bindPopup('<b>' + esc(nodeId) + '</b><br>Start of trajectory');

        var endMarker = L.circleMarker(latlngs[latlngs.length - 1], {
            radius: 5, fillColor: '#ef5350', color: '#fff', weight: 1, fillOpacity: 0.9,
        });
        endMarker.bindPopup('<b>' + esc(nodeId) + '</b><br>Latest position');

        var props = geojson.properties || {};
        trajectoryLine.bindPopup(
            '<div class="popup-title" style="color:#ffc107">Trajectory: ' + esc(nodeId) + '</div>' +
            '<div class="popup-row"><span class="popup-key">Points</span><span class="popup-val">' + coords.length + '</span></div>' +
            '<div class="popup-row"><span class="popup-key">Network</span><span class="popup-val">' + esc(props.network || '') + '</span></div>'
        );

        // Evict oldest trajectory if at capacity
        var activeIds = Object.keys(trajectoryLayers);
        if (activeIds.length >= MAX_TRAJECTORIES) {
            var evictId = activeIds[0];
            map.removeLayer(trajectoryLayers[evictId]);
            delete trajectoryLayers[evictId];
        }

        var group = L.layerGroup([trajectoryLine, startMarker, endMarker]).addTo(map);
        trajectoryLayers[nodeId] = group;

        // Fit map to trajectory bounds
        map.fitBounds(trajectoryLine.getBounds().pad(0.2));
        loadTrackedNodes(); // refresh button states
    } catch (e) {
        showToast('Failed to load trajectory: ' + e.message, true);
    }
}

function clearAllTrajectories() {
    for (var nodeId in trajectoryLayers) {
        if (trajectoryLayers.hasOwnProperty(nodeId)) {
            map.removeLayer(trajectoryLayers[nodeId]);
        }
    }
    trajectoryLayers = {};
}

// =========================================================================
// Service Worker Registration (Offline Tile Caching)
// =========================================================================

function registerServiceWorker() {
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw-tiles.js', { scope: '/' })
            .then(function(reg) {
                console.debug('SW registered:', reg.scope);
            })
            .catch(function(err) {
                console.debug('SW registration skipped:', err.message);
            });
    }
}

// =========================================================================
// WebSocket Real-Time Client
// =========================================================================

function connectWebSocket(port) {
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
        return; // Already connected or connecting
    }

    var wsUrl = 'ws://' + window.location.hostname + ':' + port;
    try {
        ws = new WebSocket(wsUrl);
    } catch (e) {
        console.debug('WebSocket not available:', e);
        return;
    }

    ws.onopen = function() {
        console.log('WebSocket connected to', wsUrl);
        wsReconnectDelay = 2000; // Reset backoff on success
        setConnectionStatus('connected', 'Live');
    };

    ws.onmessage = function(event) {
        try {
            var msg = JSON.parse(event.data);
            handleRealtimeMessage(msg);
        } catch (e) {
            console.debug('WebSocket message parse error:', e);
        }
    };

    ws.onclose = function() {
        console.debug('WebSocket closed, reconnecting in', wsReconnectDelay, 'ms');
        ws = null;
        scheduleReconnect(port);
    };

    ws.onerror = function() {
        // onclose will fire after onerror, so reconnect happens there
    };
}

function scheduleReconnect(port) {
    if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
    wsReconnectTimer = setTimeout(function() {
        connectWebSocket(port);
    }, wsReconnectDelay);
    // Exponential backoff: 2s, 4s, 8s, 16s, max 30s
    wsReconnectDelay = Math.min(wsReconnectDelay * 2, 30000);
}

function handleRealtimeMessage(msg) {
    if (msg.type === 'node.position' && msg.lat && msg.lon && msg.node_id) {
        updateOrAddNode(msg);
    }
    // Alert events from the alert engine via EventBus
    if (msg.type === 'alert.fired' && msg.data) {
        handleAlertEvent(msg.data);
    }
    // Other event types (node.info, node.telemetry, node.topology)
    // trigger a lightweight refresh of the affected node's popup if open
    if (msg.type === 'node.info' || msg.type === 'node.telemetry') {
        // These don't move markers, but signal data is fresh
        // Full refresh happens on the next polling cycle
    }
}

function updateOrAddNode(msg) {
    var nodeId = msg.node_id;
    var latlng = L.latLng(msg.lat, msg.lon);

    // O(1) lookup via marker registry
    var existing = markerRegistry.get(nodeId);
    if (existing) {
        existing.setLatLng(latlng);
        // Update corresponding feature in allFeatures so next render preserves position
        for (var i = 0; i < allFeatures.length; i++) {
            if (allFeatures[i].properties && allFeatures[i].properties.id === nodeId) {
                allFeatures[i].geometry.coordinates = [msg.lon, msg.lat];
                break;
            }
        }
    } else {
        // New node -- add a temporary marker. Full data comes on next poll.
        var network = msg.source || 'meshtastic';
        var color = NETWORK_COLORS[network] || NETWORK_COLORS.meshtastic;
        var marker = L.circleMarker(latlng, {
            radius: 6,
            fillColor: color,
            color: '#fff',
            weight: 1,
            opacity: 0.9,
            fillOpacity: 0.8,
        });
        marker.bindPopup('<b>' + esc(nodeId) + '</b><br><i>Real-time update</i>');
        markerRegistry.set(nodeId, marker);
        if (useClustering && clusterGroup) {
            clusterGroup.addLayer(marker);
        } else if (directGroup) {
            directGroup.addLayer(marker);
        }
    }
}

// =========================================================================
// Alert Panel
// =========================================================================

function toggleAlertPanel() {
    alertPanelOpen = !alertPanelOpen;
    var panel = document.getElementById('alertPanel');
    var checkbox = document.getElementById('overlayAlerts');
    if (alertPanelOpen) {
        panel.classList.add('visible');
    } else {
        panel.classList.remove('visible');
    }
    if (checkbox) checkbox.checked = alertPanelOpen;
}

function handleAlertEvent(alertData) {
    // Prepend to in-memory buffer (newest first)
    alertItems.unshift(alertData);
    if (alertItems.length > MAX_ALERT_ITEMS) {
        alertItems.length = MAX_ALERT_ITEMS;
    }
    renderAlertPanel();
    updateAlertBadge();

    // Show toast for critical alerts
    if (alertData.severity === 'critical') {
        showToast('ALERT: ' + (alertData.message || alertData.alert_type), true);
    }
}

function renderAlertPanel() {
    var body = document.getElementById('alertPanelBody');
    if (!alertItems.length) {
        body.innerHTML = '<div class="alert-empty">No alerts</div>';
        return;
    }
    var html = '';
    for (var i = 0; i < alertItems.length; i++) {
        var a = alertItems[i];
        var sevClass = 'alert-sev-' + (a.severity || 'info');
        var timeStr = '';
        if (a.timestamp) {
            try {
                timeStr = new Date(a.timestamp * 1000).toLocaleTimeString();
            } catch (e) { /* ignore */ }
        }
        html += '<div class="alert-item">' +
            '<span class="alert-severity-dot ' + sevClass + '"></span>' +
            '<div class="alert-item-body">' +
                '<div class="alert-item-msg">' + esc(a.message || a.alert_type || 'Alert') + '</div>' +
                '<div class="alert-item-meta">' +
                    esc(a.node_id || '') +
                    (timeStr ? ' &middot; ' + esc(timeStr) : '') +
                    ' &middot; ' + esc(a.severity || '') +
                '</div>' +
            '</div>' +
        '</div>';
    }
    body.innerHTML = html;
}

function updateAlertBadge() {
    var count = alertItems.length;
    var badge = document.getElementById('headerAlertBadge');
    var badgeCount = document.getElementById('headerAlertCount');
    var panelCount = document.getElementById('alertBadgeCount');
    var overlayCount = document.getElementById('countAlerts');
    badgeCount.textContent = count;
    panelCount.textContent = count;
    if (overlayCount) overlayCount.textContent = count;
    if (count > 0) {
        badge.classList.add('has-alerts');
    } else {
        badge.classList.remove('has-alerts');
    }
}

async function loadInitialAlerts() {
    try {
        var resp = await fetch(API_BASE + '/api/alerts/active');
        if (!resp.ok) return;
        var data = await resp.json();
        var alerts = data.alerts || [];
        // Load in chronological order (oldest first) so newest end up at top
        for (var i = alerts.length - 1; i >= 0; i--) {
            alertItems.push(alerts[i]);
        }
        if (alertItems.length > MAX_ALERT_ITEMS) {
            alertItems.length = MAX_ALERT_ITEMS;
        }
        renderAlertPanel();
        updateAlertBadge();
    } catch (e) {
        console.debug('Failed to load initial alerts:', e);
    }
}

// =========================================================================
// Analytics Panel
// =========================================================================

let analyticsPanelOpen = false;
let analyticsActiveTab = 'growth';
let analyticsCache = {};  // Cache fetched data per tab

function toggleAnalyticsPanel() {
    analyticsPanelOpen = !analyticsPanelOpen;
    var panel = document.getElementById('analyticsPanel');
    var checkbox = document.getElementById('overlayAnalytics');
    // Hide history panel when analytics opens (same position)
    if (analyticsPanelOpen && historyPanelOpen) {
        toggleTrajectoryPanel();
    }
    if (analyticsPanelOpen) {
        panel.classList.add('visible');
        loadAnalyticsTab(analyticsActiveTab);
    } else {
        panel.classList.remove('visible');
    }
    if (checkbox) checkbox.checked = analyticsPanelOpen;
}

function switchAnalyticsTab(tab) {
    analyticsActiveTab = tab;
    var tabs = document.querySelectorAll('.analytics-tab');
    for (var i = 0; i < tabs.length; i++) {
        tabs[i].classList.toggle('active', tabs[i].dataset.tab === tab);
    }
    loadAnalyticsTab(tab);
}

async function loadAnalyticsTab(tab) {
    var body = document.getElementById('analyticsBody');
    body.innerHTML = '<div style="color:#546e7a;font-size:11px;text-align:center;padding:24px">Loading...</div>';

    try {
        if (tab === 'growth') {
            var resp = await fetch(API_BASE + '/api/analytics/growth');
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var data = await resp.json();
            analyticsCache.growth = data;
            renderGrowthChart(data, body);
        } else if (tab === 'activity') {
            var resp = await fetch(API_BASE + '/api/analytics/activity');
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var data = await resp.json();
            analyticsCache.activity = data;
            renderActivityHeatmap(data, body);
        } else if (tab === 'ranking') {
            var resp = await fetch(API_BASE + '/api/analytics/ranking');
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var data = await resp.json();
            analyticsCache.ranking = data;
            renderRankingTable(data, body);
        } else if (tab === 'alerts') {
            var summaryResp = await fetch(API_BASE + '/api/analytics/alert-trends');
            if (!summaryResp.ok) throw new Error('HTTP ' + summaryResp.status);
            var data = await summaryResp.json();
            analyticsCache.alerts = data;
            renderAlertTrends(data, body);
        }
    } catch (e) {
        body.innerHTML = '<div style="color:#ef5350;font-size:11px;text-align:center;padding:24px">Failed to load analytics: ' + esc(e.message) + '</div>';
    }
}

function renderGrowthChart(data, container) {
    var buckets = data.buckets || [];
    if (buckets.length === 0) {
        container.innerHTML = '<div style="color:#546e7a;font-size:11px;text-align:center;padding:24px">No growth data available</div>';
        return;
    }

    // Summary stats
    var maxNodes = 0, totalObs = 0;
    for (var i = 0; i < buckets.length; i++) {
        if (buckets[i].unique_nodes > maxNodes) maxNodes = buckets[i].unique_nodes;
        totalObs += buckets[i].observations;
    }
    var latestNodes = buckets[buckets.length - 1].unique_nodes;

    var html = '<div class="analytics-summary-grid">' +
        '<div class="analytics-stat"><div class="analytics-stat-value">' + latestNodes + '</div><div class="analytics-stat-label">Current Nodes</div></div>' +
        '<div class="analytics-stat"><div class="analytics-stat-value">' + maxNodes + '</div><div class="analytics-stat-label">Peak Nodes</div></div>' +
        '<div class="analytics-stat"><div class="analytics-stat-value">' + totalObs + '</div><div class="analytics-stat-label">Observations</div></div>' +
        '</div>';

    // SVG sparkline
    var chartW = 396, chartH = 90, padT = 8, padB = 16, padL = 30, padR = 8;
    var plotW = chartW - padL - padR, plotH = chartH - padT - padB;
    var maxY = maxNodes || 1;

    var points = [];
    var areaPoints = [];
    for (var i = 0; i < buckets.length; i++) {
        var x = padL + (i / Math.max(1, buckets.length - 1)) * plotW;
        var y = padT + plotH - (buckets[i].unique_nodes / maxY) * plotH;
        points.push(x.toFixed(1) + ',' + y.toFixed(1));
        areaPoints.push(x.toFixed(1) + ',' + y.toFixed(1));
    }
    // Close area path
    areaPoints.push((padL + plotW).toFixed(1) + ',' + (padT + plotH).toFixed(1));
    areaPoints.push(padL.toFixed(1) + ',' + (padT + plotH).toFixed(1));

    html += '<div class="analytics-chart"><svg viewBox="0 0 ' + chartW + ' ' + chartH + '">' +
        '<polygon points="' + areaPoints.join(' ') + '" fill="rgba(79,195,247,0.1)" />' +
        '<polyline points="' + points.join(' ') + '" fill="none" stroke="#4fc3f7" stroke-width="1.5" />';

    // Y-axis labels
    html += '<text x="' + (padL - 4) + '" y="' + (padT + 4) + '" fill="#546e7a" font-size="8" text-anchor="end">' + maxY + '</text>';
    html += '<text x="' + (padL - 4) + '" y="' + (padT + plotH) + '" fill="#546e7a" font-size="8" text-anchor="end">0</text>';

    // X-axis labels (first and last timestamps)
    var firstTs = new Date(buckets[0].timestamp * 1000);
    var lastTs = new Date(buckets[buckets.length - 1].timestamp * 1000);
    html += '<text x="' + padL + '" y="' + (chartH - 2) + '" fill="#546e7a" font-size="8">' + firstTs.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) + '</text>';
    html += '<text x="' + (chartW - padR) + '" y="' + (chartH - 2) + '" fill="#546e7a" font-size="8" text-anchor="end">' + lastTs.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) + '</text>';

    html += '</svg></div>';
    html += '<div style="font-size:10px;color:#546e7a;text-align:center">Network growth over ' + buckets.length + ' time buckets (' + (data.bucket_seconds / 3600).toFixed(0) + 'h each)</div>';

    container.innerHTML = html;
}

function renderActivityHeatmap(data, container) {
    var hours = data.hours || [];
    if (hours.length === 0) {
        container.innerHTML = '<div style="color:#546e7a;font-size:11px;text-align:center;padding:24px">No activity data available</div>';
        return;
    }

    var maxVal = Math.max.apply(null, hours) || 1;
    var totalObs = data.total_observations || 0;
    var peakHour = data.peak_hour;

    var html = '<div class="analytics-summary-grid">' +
        '<div class="analytics-stat"><div class="analytics-stat-value">' + totalObs + '</div><div class="analytics-stat-label">Total Obs.</div></div>' +
        '<div class="analytics-stat"><div class="analytics-stat-value">' + (peakHour != null ? peakHour + ':00' : '--') + '</div><div class="analytics-stat-label">Peak Hour</div></div>' +
        '<div class="analytics-stat"><div class="analytics-stat-value">' + Math.round(totalObs / 24) + '</div><div class="analytics-stat-label">Avg / Hour</div></div>' +
        '</div>';

    html += '<div style="font-size:10px;color:#78909c;margin-bottom:6px">Activity by Hour of Day (UTC)</div>';
    html += '<div class="heatmap-grid">';
    for (var h = 0; h < 24; h++) {
        var intensity = hours[h] / maxVal;
        var r = Math.round(79 * intensity);
        var g = Math.round(195 * intensity);
        var b = Math.round(247 * intensity);
        var bgColor = 'rgba(' + r + ',' + g + ',' + b + ',' + (0.15 + intensity * 0.7) + ')';
        html += '<div class="heatmap-cell" style="background:' + bgColor + '" data-tip="' + h + ':00 — ' + hours[h] + ' obs"></div>';
    }
    html += '</div>';

    // Bar chart for hours
    var barChartW = 396, barChartH = 80, barPadL = 24, barPadR = 8, barPadT = 4, barPadB = 14;
    var barPlotW = barChartW - barPadL - barPadR;
    var barPlotH = barChartH - barPadT - barPadB;
    var barW = barPlotW / 24 - 1;

    html += '<div class="analytics-chart" style="height:80px"><svg viewBox="0 0 ' + barChartW + ' ' + barChartH + '">';
    for (var h = 0; h < 24; h++) {
        var barH = (hours[h] / maxVal) * barPlotH;
        var barX = barPadL + h * (barW + 1);
        var barY = barPadT + barPlotH - barH;
        var barColor = h === peakHour ? '#4fc3f7' : 'rgba(79,195,247,0.4)';
        html += '<rect x="' + barX.toFixed(1) + '" y="' + barY.toFixed(1) + '" width="' + barW.toFixed(1) + '" height="' + barH.toFixed(1) + '" fill="' + barColor + '" rx="1" />';
    }
    // X labels
    for (var h = 0; h < 24; h += 6) {
        html += '<text x="' + (barPadL + h * (barW + 1) + barW / 2).toFixed(1) + '" y="' + (barChartH - 2) + '" fill="#546e7a" font-size="8" text-anchor="middle">' + h + 'h</text>';
    }
    html += '</svg></div>';

    container.innerHTML = html;
}

function renderRankingTable(data, container) {
    var nodes = data.nodes || [];
    if (nodes.length === 0) {
        container.innerHTML = '<div style="color:#546e7a;font-size:11px;text-align:center;padding:24px">No ranking data available</div>';
        return;
    }

    var html = '<table class="ranking-table"><thead><tr>' +
        '<th>#</th><th>Node</th><th>Network</th><th style="text-align:right">Obs</th><th style="text-align:right">Active</th>' +
        '</tr></thead><tbody>';

    for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        var activeMins = Math.round(n.active_seconds / 60);
        var activeStr = activeMins > 60 ? (activeMins / 60).toFixed(1) + 'h' : activeMins + 'm';
        html += '<tr>' +
            '<td class="ranking-rank">' + (i + 1) + '</td>' +
            '<td class="ranking-node">' + esc(n.node_id) + '</td>' +
            '<td class="ranking-network">' + esc(n.network || '') + '</td>' +
            '<td class="ranking-count">' + n.observation_count + '</td>' +
            '<td class="ranking-count">' + activeStr + '</td>' +
            '</tr>';
    }

    html += '</tbody></table>';
    container.innerHTML = html;
}

function renderAlertTrends(data, container) {
    var buckets = data.buckets || [];
    if (buckets.length === 0) {
        container.innerHTML = '<div style="color:#546e7a;font-size:11px;text-align:center;padding:24px">No alert trend data available</div>';
        return;
    }

    var totalAlerts = data.total_alerts || 0;
    var totalCritical = 0, totalWarning = 0, totalInfo = 0;
    var maxBucketTotal = 0;
    for (var i = 0; i < buckets.length; i++) {
        totalCritical += buckets[i].critical || 0;
        totalWarning += buckets[i].warning || 0;
        totalInfo += buckets[i].info || 0;
        if (buckets[i].total > maxBucketTotal) maxBucketTotal = buckets[i].total;
    }

    var html = '<div class="analytics-summary-grid">' +
        '<div class="analytics-stat"><div class="analytics-stat-value" style="color:#ef5350">' + totalCritical + '</div><div class="analytics-stat-label">Critical</div></div>' +
        '<div class="analytics-stat"><div class="analytics-stat-value" style="color:#ffa726">' + totalWarning + '</div><div class="analytics-stat-label">Warning</div></div>' +
        '<div class="analytics-stat"><div class="analytics-stat-value" style="color:#42a5f5">' + totalInfo + '</div><div class="analytics-stat-label">Info</div></div>' +
        '</div>';

    // Stacked bar chart
    var chartW = 396, chartH = 100, padL = 30, padR = 8, padT = 8, padB = 16;
    var plotW = chartW - padL - padR, plotH = chartH - padT - padB;
    var maxY = maxBucketTotal || 1;
    var barW = Math.max(3, plotW / buckets.length - 1);

    html += '<div class="analytics-chart"><svg viewBox="0 0 ' + chartW + ' ' + chartH + '">';

    for (var i = 0; i < buckets.length; i++) {
        var x = padL + i * (barW + 1);
        var b = buckets[i];

        // Stack: info (bottom), warning (middle), critical (top)
        var infoH = ((b.info || 0) / maxY) * plotH;
        var warnH = ((b.warning || 0) / maxY) * plotH;
        var critH = ((b.critical || 0) / maxY) * plotH;

        var baseY = padT + plotH;
        if (infoH > 0) {
            html += '<rect x="' + x.toFixed(1) + '" y="' + (baseY - infoH).toFixed(1) + '" width="' + barW.toFixed(1) + '" height="' + infoH.toFixed(1) + '" fill="#42a5f5" rx="1" />';
        }
        baseY -= infoH;
        if (warnH > 0) {
            html += '<rect x="' + x.toFixed(1) + '" y="' + (baseY - warnH).toFixed(1) + '" width="' + barW.toFixed(1) + '" height="' + warnH.toFixed(1) + '" fill="#ffa726" rx="1" />';
        }
        baseY -= warnH;
        if (critH > 0) {
            html += '<rect x="' + x.toFixed(1) + '" y="' + (baseY - critH).toFixed(1) + '" width="' + barW.toFixed(1) + '" height="' + critH.toFixed(1) + '" fill="#ef5350" rx="1" />';
        }
    }

    // Y-axis
    html += '<text x="' + (padL - 4) + '" y="' + (padT + 4) + '" fill="#546e7a" font-size="8" text-anchor="end">' + maxY + '</text>';
    html += '<text x="' + (padL - 4) + '" y="' + (padT + plotH) + '" fill="#546e7a" font-size="8" text-anchor="end">0</text>';

    html += '</svg></div>';
    html += '<div style="font-size:10px;color:#546e7a;text-align:center">' + totalAlerts + ' total alerts across ' + buckets.length + ' time buckets</div>';

    container.innerHTML = html;
}

function exportAnalytics(format) {
    var data = analyticsCache[analyticsActiveTab];
    if (!data) {
        showToast('No data to export', true);
        return;
    }

    var content, filename, mimeType;
    if (format === 'json') {
        content = JSON.stringify(data, null, 2);
        filename = 'meshforge_analytics_' + analyticsActiveTab + '.json';
        mimeType = 'application/json';
    } else {
        // CSV export
        content = analyticsDataToCsv(analyticsActiveTab, data);
        filename = 'meshforge_analytics_' + analyticsActiveTab + '.csv';
        mimeType = 'text/csv';
    }

    var blob = new Blob([content], { type: mimeType });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast('Exported ' + filename);
}

function analyticsDataToCsv(tab, data) {
    var lines = [];
    if (tab === 'growth') {
        lines.push('timestamp,unique_nodes,observations');
        var buckets = data.buckets || [];
        for (var i = 0; i < buckets.length; i++) {
            var b = buckets[i];
            lines.push(b.timestamp + ',' + b.unique_nodes + ',' + b.observations);
        }
    } else if (tab === 'activity') {
        lines.push('hour,observation_count');
        var hours = data.hours || [];
        for (var h = 0; h < hours.length; h++) {
            lines.push(h + ',' + hours[h]);
        }
    } else if (tab === 'ranking') {
        lines.push('rank,node_id,network,observation_count,active_seconds');
        var nodes = data.nodes || [];
        for (var i = 0; i < nodes.length; i++) {
            var n = nodes[i];
            lines.push((i + 1) + ',' + n.node_id + ',' + (n.network || '') + ',' + n.observation_count + ',' + n.active_seconds);
        }
    } else if (tab === 'alerts') {
        lines.push('timestamp,critical,warning,info,total');
        var buckets = data.buckets || [];
        for (var i = 0; i < buckets.length; i++) {
            var b = buckets[i];
            lines.push(b.timestamp + ',' + (b.critical || 0) + ',' + (b.warning || 0) + ',' + (b.info || 0) + ',' + (b.total || 0));
        }
    }
    return lines.join('\n');
}

// =========================================================================
// Health Check (data staleness indicator)
// =========================================================================

async function checkDataHealth() {
    try {
        const resp = await fetch(API_BASE + '/api/health');
        if (!resp.ok) return;
        const health = await resp.json();
        if (health.status === 'critical') {
            setConnectionStatus('error', 'Critical');
            showToast('Health: critical -- sources may be unreachable', true);
        } else if (health.status === 'degraded') {
            setConnectionStatus('error', 'Degraded');
            showToast('Health: degraded -- some sources failing', true);
        }
    } catch (e) {
        // Silently ignore health check failures
    }
}

// =========================================================================
// Auto-refresh
// =========================================================================

// Auto-refresh interval (silent -- no toast on auto-refresh, upstream improvement)
setInterval(function() {
    loadNodeData();
    if (showTopology) loadTopologyData();
}, 60 * 1000); // Refresh every 60 seconds (WebSocket fallback)

// Periodic health check (every 2 minutes)
setInterval(checkDataHealth, 2 * 60 * 1000);

// =========================================================================
// Init
// =========================================================================

document.addEventListener('DOMContentLoaded', function() {
    initMap();
    registerServiceWorker();
});
