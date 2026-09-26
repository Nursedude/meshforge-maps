"""Map refresh must not re-cluster or re-paint what did not change
(operator 2026-09-25: "maps redraw — it rolls").

- setLatLng fires 'move'; markercluster re-inserts a moved marker into its
  tree, so an unconditional call re-clustered every node each 60 s refresh.
- The heatmap was removed and re-created every refresh (canvas blank+repaint);
  Leaflet.heat's setLatLngs updates it in place.
"""
import os

JS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "js",
                       "meshforge-maps.js"), encoding="utf-8").read()


def test_existing_marker_moves_only_on_a_real_change():
    upd = JS[JS.index("// Update in place — no allocation"):]
    upd = upd[:upd.index("existing.setStyle(style);")]
    assert "existing.getLatLng()" in upd
    assert "if (cur.lat !== lat || cur.lng !== lon)" in upd


def test_heatmap_updates_in_place_and_clears_when_empty():
    fn = JS[JS.index("function renderCoverageHeatmap(data)"):]
    fn = fn[:fn.index("\nfunction ", 10)]
    assert "heatmapLayer.setLatLngs(points)" in fn
    empty = fn[fn.index("if (points.length === 0)"):]
    empty = empty[:empty.index("return;")]
    assert "map.removeLayer(heatmapLayer)" in empty


def test_zoom_gap_is_dark_not_leaflet_default_white():
    """Zoom animations stay off (Pi GPU), so the gap between zoom levels shows
    the container — it must be the page's dark background, not #ddd."""
    css = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "css",
                            "meshforge-maps.css"), encoding="utf-8").read()
    assert ".leaflet-container { background: #0a0e1a; }" in css
