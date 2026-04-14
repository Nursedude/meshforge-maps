# The White Screen and the Zombie Layer

*A debugging post-mortem from MeshForge Maps — on what happens when you and
an AI spend fifteen commits chasing a bug neither of you is looking at.*

---

A Raspberry Pi in the lab/QTH, running the map 24 hours a day. Click zoom —
the tiles vanish, the screen goes white, and nothing comes back until you
hard-reload. Four rounds of "fixes" ship. None of them hold. That was the
shape of the last two days on `meshforge-maps`, and the pattern is worth
writing down because it's the exact failure mode that makes human + AI
collaboration fall on its face.

## The trail

Fifteen commits between `af861d2` and `ee54029`. Grouped by what we
*thought* we were doing:

```
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 1  Pi 24h stability           8516909 … 83b0454              │
│  (auto-restart hook, WAL checkpoint, thread leak, dict pruning,     │
│   CPU cache for JSON)                                               │
│                      │                                              │
│                      ▼                                              │
│  STAGE 2  Tile reliability guesses   f633fdd … 4b5df9a              │
│  (SW blank-tile path, CSS3 animations off for Pi GPU,               │
│   watchdog softened) ← 4 rounds, none worked                        │
│                      │                                              │
│                      ▼                                              │
│  STAGE 3  Stop guessing, instrument  bce5a3b, ad5e5e3               │
│  (expose map globally, log handler ctx on zoom) ← the pivot         │
│                      │                                              │
│                      ▼                                              │
│  STAGE 4  Real root cause            a88f2a5                        │
│  (duplicate tile layer from loadConfig → changeTileLayer)           │
│                      │                                              │
│                      ▼                                              │
│  STAGE 5  Ship it (cache-bust)       ee54029                        │
│  (SW v1→v2 plus ?v=a88f2a5 on the script src)                       │
└─────────────────────────────────────────────────────────────────────┘
```

Stage 2 is the embarrassing one. Each fix looked real for about an hour.
Disabling CSS3 animations — Pi GPU, plausible. Rewriting the service
worker's blank-tile response so it stopped bypassing retry — plausible.
Softening the tile watchdog so it stopped killing still-loading tiles —
plausible. None of them fixed anything. The white screen came back every
single time.

## The turn

The fix, when it arrived, took five minutes. What it took to get there was
two diagnostic commits — `bce5a3b` and `ad5e5e3` — whose only purpose was
to stop patching and start measuring.

We exposed the Leaflet map object to `window._map` so it survived console
reloads. We logged every registered event handler's context on zoom —
specifically, for each handler, was `this._map` still attached? The bug
announced itself immediately:

```
GridLayer._resetView → this._map.getCenter() → null
```

Two tile-layer handlers registered. One of them had `hasMap=false`. A
zombie. It had been removed from the map but its `zoom` and `viewreset`
handlers were still in the registry, and on the next zoom, the dead
handler's `_resetView` fired against a null map and threw. The throw
aborted zoom completion — which is why tiles never rendered. That was the
white screen.

Where did the zombie come from? `loadConfig()` called `changeTileLayer()`
on every page load, even when the current provider already matched the
requested one. First layer got torn down, second layer got built, most of
the first layer's handlers got cleaned up — but not all of them.

## The fix

Two lines. Skip `changeTileLayer()` when the requested provider is already
active. That's it. That's the entire fix (`a88f2a5`).

Then one more commit (`ee54029`) to prove it worked. The service worker
was still serving yesterday's `meshforge-maps.js` out of `STATIC_CACHE`,
so the fix was live on the server and invisible in the browser. Bump the
cache key v1 → v2 and append `?v=a88f2a5` to the script URL. *Now* the
white screen is gone.

## What not to do next time

**Don't patch symptoms in a loop.** Four plausible fixes that don't move
the needle isn't a signal to try a fifth. It's a signal to instrument.
Stage 3 should have been Stage 2.

**Don't trust "the cache is fine" in a service-worker app.** If your
frontend ships through an SW with a static cache, every JS fix needs a
version bump or you'll spend an afternoon debugging a bug you already
fixed. Put the cache-busting query parameter in the template and forget
about it.

**Don't let the AI drive diagnosis from hypothesis alone.** The model is
great at generating candidate causes — GPU animations, watchdog races, SW
edge cases, all genuinely plausible. That's exactly the problem. When
every hypothesis looks good, the AI will keep producing them, and a human
operator who defers to "this should fix it" will keep shipping them. The
collaboration that worked wasn't parallel guessing. It was one side —
the human — refusing a fifth round until we had evidence the fourth
round's target was even real.

## Close

I keep coming back to the same thought watching this kind of debugging.
Failure and success aren't opposite motions. They're the same motion. Four
wrong fixes taught us exactly where the bug wasn't, and that's what made
the fifth attempt take five minutes instead of another day. The human
trait at the center of it is diagnostic patience — the willingness to
stop, measure, and be wrong on the record before being right. That's not
something the model will volunteer. You have to bring it to the session.

Find us on the mesh.

— nursedude & Claude (Opus 4.6, 1M context)
