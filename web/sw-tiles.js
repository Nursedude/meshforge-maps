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
const MAX_TILE_CACHE_ITEMS = 2000;  // LRU eviction threshold

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
    event.waitUntil(
        caches.keys().then((names) => {
            return Promise.all(
                names
                    .filter((name) => name !== CACHE_NAME && name !== STATIC_CACHE)
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
        // API: Network-first, falling back to cache
        event.respondWith(networkFirst(event.request, STATIC_CACHE));
    } else {
        // Everything else: stale-while-revalidate
        event.respondWith(staleWhileRevalidate(event.request, STATIC_CACHE));
    }
});

/**
 * Cache-first strategy: return cached response, fetch from network on miss.
 */
async function cacheFirst(request, cacheName) {
    const cache = await caches.open(cacheName);
    const cached = await cache.match(request);
    if (cached) {
        return cached;
    }
    try {
        const response = await fetch(request);
        if (response.ok) {
            // Clone and cache the response
            cache.put(request, response.clone());
            // Async eviction (don't block response)
            enforceCacheLimit(cacheName, MAX_TILE_CACHE_ITEMS);
        }
        return response;
    } catch (error) {
        // Network failure with no cache -- return offline placeholder
        return new Response('', {
            status: 503,
            statusText: 'Offline - tile not cached',
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
    if (event.data && event.data.type === 'CLEAR_TILE_CACHE') {
        event.waitUntil(
            caches.delete(CACHE_NAME).then(() => {
                event.ports[0]?.postMessage({ cleared: true });
            })
        );
    }

    if (event.data && event.data.type === 'CACHE_STATS') {
        event.waitUntil(
            caches.open(CACHE_NAME).then(async (cache) => {
                const keys = await cache.keys();
                event.ports[0]?.postMessage({
                    tileCount: keys.length,
                    maxTiles: MAX_TILE_CACHE_ITEMS,
                });
            })
        );
    }
});
