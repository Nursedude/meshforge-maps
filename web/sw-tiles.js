/**
 * MeshForge Maps - Service Worker for Offline Tile Caching
 *
 * Caches map tiles from all configured tile providers for offline use.
 * Matches the meshforge core sw-tiles.js pattern:
 *   - Cache-first strategy for tile requests
 *   - Network-first for API endpoints
 *   - Stale-while-revalidate for static assets
 *   - Configurable cache size limits with LRU eviction
 *
 * Tile domains cached:
 *   - *.basemaps.cartocdn.com   (CartoDB Dark Matter)
 *   - tile.openstreetmap.org    (OSM)
 *   - tile.opentopomap.org      (OpenTopoMap)
 *   - server.arcgisonline.com   (Esri Satellite/Topo)
 *   - tiles.stadiamaps.com      (Stadia Terrain)
 */

const CACHE_NAME = 'meshforge-maps-tiles-v1';
const STATIC_CACHE = 'meshforge-maps-static-v1';
const API_CACHE = 'meshforge-maps-api-v1';
const MAX_TILE_CACHE_ITEMS = 500;  // LRU eviction threshold (reduced for Pi/constrained devices)
const API_CACHE_MAX_AGE_MS = 15 * 60 * 1000; // 15 minutes for API responses

// 1x1 transparent PNG returned for uncached tiles when offline
const BLANK_TILE = Uint8Array.from(atob(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQABNjN9GQ=='
), c => c.charCodeAt(0));

// Tile domains to cache
const TILE_DOMAINS = [
    'basemaps.cartocdn.com',
    'tile.openstreetmap.org',
    'tile.opentopomap.org',
    'server.arcgisonline.com',
    'tiles.stadiamaps.com',
];

// Static assets to precache
const STATIC_ASSETS = [
    '/',
    '/meshforge_maps.html',
];

// CDN libraries to cache on first use
const CDN_DOMAINS = [
    'unpkg.com',
    'cdnjs.cloudflare.com',
    'd3js.org',
];

/**
 * Determine if a URL is a map tile request.
 */
function isTileRequest(url) {
    try {
        const hostname = new URL(url).hostname;
        return TILE_DOMAINS.some(domain => hostname.endsWith(domain));
    } catch {
        return false;
    }
}

/**
 * Determine if a URL is a CDN asset (Leaflet, D3, etc.).
 */
function isCDNRequest(url) {
    try {
        const hostname = new URL(url).hostname;
        return CDN_DOMAINS.some(domain => hostname.endsWith(domain));
    } catch {
        return false;
    }
}

/**
 * Determine if a URL is an API request.
 */
function isAPIRequest(url) {
    try {
        return new URL(url).pathname.startsWith('/api/');
    } catch {
        return false;
    }
}

/**
 * Enforce cache size limit with LRU eviction.
 */
async function enforceCacheLimit(cacheName, maxItems) {
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    if (keys.length > maxItems) {
        // Evict oldest entries (first in cache = oldest)
        const evictCount = keys.length - maxItems;
        for (let i = 0; i < evictCount; i++) {
            await cache.delete(keys[i]);
        }
    }
}

// ---------------------------------------------------------------------------
// Service Worker Lifecycle
// ---------------------------------------------------------------------------

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(STATIC_CACHE).then((cache) => {
            // Precache static assets (best-effort, don't fail install)
            return cache.addAll(STATIC_ASSETS).catch(() => {
                console.debug('SW: Some static assets could not be precached');
            });
        })
    );
    // Activate immediately without waiting for existing clients
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    const KEEP_CACHES = [CACHE_NAME, STATIC_CACHE, API_CACHE];
    event.waitUntil(
        caches.keys().then((names) => {
            return Promise.all(
                names
                    .filter((name) => !KEEP_CACHES.includes(name))
                    .map((name) => caches.delete(name))
            );
        }).then(() => self.clients.claim())
    );
});

// ---------------------------------------------------------------------------
// Fetch Strategy
// ---------------------------------------------------------------------------

self.addEventListener('fetch', (event) => {
    const url = event.request.url;

    if (isTileRequest(url)) {
        // TILES: Cache-first, falling back to network
        event.respondWith(cacheFirst(event.request, CACHE_NAME));
    } else if (isCDNRequest(url)) {
        // CDN ASSETS: Cache-first (Leaflet, D3, etc.)
        event.respondWith(cacheFirst(event.request, STATIC_CACHE));
    } else if (isAPIRequest(url)) {
        // API: Network-first, falling back to cache (dedicated API cache)
        event.respondWith(networkFirst(event.request, API_CACHE));
    } else {
        // Everything else: stale-while-revalidate
        event.respondWith(staleWhileRevalidate(event.request, STATIC_CACHE));
    }
});

/**
 * Cache-first strategy with LRU semantics.
 *
 * On cache hit: delete and re-insert the entry so it moves to the end
 * of the cache (most recently used). enforceCacheLimit evicts from the
 * beginning (least recently used).
 */
async function cacheFirst(request, cacheName) {
    const cache = await caches.open(cacheName);
    const cached = await cache.match(request);
    if (cached) {
        // LRU touch: move to end by re-inserting
        cache.delete(request).then(() => {
            cache.put(request, cached.clone());
        });
        return cached;
    }
    try {
        const response = await fetch(request);
        if (response.ok) {
            // Clone and cache the response
            cache.put(request, response.clone());
            // Amortized eviction: only check every ~100 inserts
            if (Math.random() < 0.01) {
                enforceCacheLimit(cacheName, MAX_TILE_CACHE_ITEMS);
            }
        }
        return response;
    } catch (error) {
        // Network failure with no cache -- return transparent placeholder tile
        return new Response(BLANK_TILE.buffer, {
            status: 200,
            headers: { 'Content-Type': 'image/png' },
        });
    }
}

/**
 * Network-first strategy: try network, fall back to cache.
 */
async function networkFirst(request, cacheName) {
    try {
        const response = await fetch(request);
        if (response.ok) {
            const cache = await caches.open(cacheName);
            cache.put(request, response.clone());
        }
        return response;
    } catch (error) {
        const cached = await caches.match(request);
        if (cached) {
            return cached;
        }
        return new Response(JSON.stringify({ error: 'offline' }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' },
        });
    }
}

/**
 * Stale-while-revalidate: return cache immediately, update in background.
 */
async function staleWhileRevalidate(request, cacheName) {
    const cache = await caches.open(cacheName);
    const cached = await cache.match(request);

    const networkPromise = fetch(request).then((response) => {
        if (response.ok) {
            cache.put(request, response.clone());
        }
        return response;
    }).catch(() => null);

    return cached || await networkPromise || new Response('', { status: 503 });
}

// ---------------------------------------------------------------------------
// Message handling for cache management
// ---------------------------------------------------------------------------

self.addEventListener('message', (event) => {
    if (event.origin && event.origin !== self.location.origin) return;
    if (!event.data || !event.data.type) return;

    if (event.data.type === 'CLEAR_TILE_CACHE') {
        event.waitUntil(
            caches.delete(CACHE_NAME).then(() => {
                event.ports[0]?.postMessage({ cleared: true });
            })
        );
    }

    if (event.data.type === 'CLEAR_ALL_CACHES') {
        event.waitUntil(
            Promise.all([
                caches.delete(CACHE_NAME),
                caches.delete(API_CACHE),
            ]).then(() => {
                event.ports[0]?.postMessage({ cleared: true });
            })
        );
    }

    if (event.data.type === 'CACHE_STATS') {
        event.waitUntil(
            Promise.all([
                caches.open(CACHE_NAME).then(c => c.keys()),
                caches.open(API_CACHE).then(c => c.keys()).catch(() => []),
                caches.open(STATIC_CACHE).then(c => c.keys()).catch(() => []),
            ]).then(([tileKeys, apiKeys, staticKeys]) => {
                event.ports[0]?.postMessage({
                    tileCount: tileKeys.length,
                    apiCount: apiKeys.length,
                    staticCount: staticKeys.length,
                    maxTiles: MAX_TILE_CACHE_ITEMS,
                });
            })
        );
    }

    if (event.data.type === 'PRECACHE_REGION') {
        // Pre-cache tiles for a geographic region at specified zoom levels.
        // Message format: { type: 'PRECACHE_REGION', tileUrlTemplate, bounds, minZoom, maxZoom }
        // bounds: { north, south, east, west }
        event.waitUntil(precacheRegion(event.data, event.ports[0]));
    }
});

// ---------------------------------------------------------------------------
// Region pre-caching for offline use
// ---------------------------------------------------------------------------

/**
 * Calculate tile coordinates for a lat/lon at a given zoom level.
 */
function latLonToTile(lat, lon, zoom) {
    const n = Math.pow(2, zoom);
    const x = Math.floor((lon + 180) / 360 * n);
    const latRad = lat * Math.PI / 180;
    const y = Math.floor((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2 * n);
    return { x: Math.max(0, Math.min(n - 1, x)), y: Math.max(0, Math.min(n - 1, y)) };
}

/**
 * Pre-cache tiles for a geographic region.
 * Rate-limited to avoid overloading tile servers.
 */
async function precacheRegion(data, port) {
    const { tileUrlTemplate, bounds, minZoom, maxZoom } = data;
    if (!tileUrlTemplate || !bounds) {
        port?.postMessage({ error: 'Missing tileUrlTemplate or bounds' });
        return;
    }

    const cache = await caches.open(CACHE_NAME);
    let cached = 0;
    let skipped = 0;
    let failed = 0;
    const maxTiles = 500; // Safety limit per precache request
    let total = 0;

    const effectiveMinZoom = minZoom || 1;
    const effectiveMaxZoom = Math.min(maxZoom || 12, 14); // Cap at z14

    for (let z = effectiveMinZoom; z <= effectiveMaxZoom && total < maxTiles; z++) {
        const topLeft = latLonToTile(bounds.north, bounds.west, z);
        const bottomRight = latLonToTile(bounds.south, bounds.east, z);

        for (let x = topLeft.x; x <= bottomRight.x && total < maxTiles; x++) {
            for (let y = topLeft.y; y <= bottomRight.y && total < maxTiles; y++) {
                total++;
                const url = tileUrlTemplate
                    .replace('{z}', z)
                    .replace('{x}', x)
                    .replace('{y}', y)
                    .replace('{s}', 'a')  // Use subdomain 'a' for precaching
                    .replace('{r}', '');   // Non-retina tiles for precaching
                try {
                    const existing = await cache.match(url);
                    if (existing) {
                        skipped++;
                        continue;
                    }
                    const response = await fetch(url);
                    if (response.ok) {
                        await cache.put(url, response);
                        cached++;
                    } else {
                        failed++;
                    }
                    // Rate limit: small delay between fetches
                    await new Promise(r => setTimeout(r, 50));
                } catch {
                    failed++;
                }
            }
        }
    }

    await enforceCacheLimit(CACHE_NAME, MAX_TILE_CACHE_ITEMS);
    port?.postMessage({ cached, skipped, failed, total });
}
