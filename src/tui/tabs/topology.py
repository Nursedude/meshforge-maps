"""Topology tab — ASCII mesh topology visualization, adjacency maps, link quality."""

import curses
from typing import Any, Dict, List, Tuple

from ..helpers import (
    CP_HEALTH_EXCELLENT,
    CP_HEALTH_FAIR,
    CP_HEALTH_GOOD,
    CP_HEALTH_POOR,
    CP_HIGHLIGHT,
    _quality_color,
    safe_addstr,
)


def draw_topology(win: Any, top: int, height: int, cols: int,
                  cache: Dict[str, Any], scroll: int) -> None:
    """Draw ASCII topology visualization of mesh links."""
    topo_data = cache.get("topo") or {}
    features = topo_data.get("features", [])
    nodes_data = cache.get("nodes") or {}
    node_features = nodes_data.get("features", [])

    lines: List[Tuple[str, int]] = []
    lines.append(("", 0))

    # Build node name lookup
    node_names: Dict[str, str] = {}
    for feat in node_features:
        props = feat.get("properties", {})
        nid = props.get("id", props.get("node_id", ""))
        name = props.get("name", props.get("long_name", str(nid)[:8]))
        node_names[str(nid)] = str(name)[:12]

    # Build adjacency from topology links
    links: List[Dict[str, Any]] = []
    adjacency: Dict[str, List[Tuple[str, float, str]]] = {}
    for feat in features:
        props = feat.get("properties", {})
        src = str(props.get("source", ""))
        tgt = str(props.get("target", ""))
        snr = props.get("snr", 0)
        quality = props.get("quality", "unknown")
        if src and tgt:
            links.append({"source": src, "target": tgt, "snr": snr,
                          "quality": quality})
            adjacency.setdefault(src, []).append((tgt, snr, quality))
            adjacency.setdefault(tgt, []).append((src, snr, quality))

    link_count = len(links)
    node_count = len(adjacency)

    lines.append((" MESH TOPOLOGY", curses.A_BOLD | curses.A_UNDERLINE))
    lines.append((f"  {node_count} nodes, {link_count} links", 0))
    lines.append(("", 0))

    if not links:
        lines.append(("  No topology data available.", curses.A_DIM))
        lines.append(("  Topology requires active mesh links (MQTT/AREDN).", 0))
    else:
        # Link quality legend
        lines.append((" LINK QUALITY LEGEND", curses.A_BOLD | curses.A_UNDERLINE))
        lines.append(("  === excellent (SNR>=8dB)   --- good (5-8dB)   "
                      "... marginal (0-5dB)   ~~~ poor (<0dB)",
                      curses.A_DIM))
        lines.append(("", 0))

        # ASCII topology: show each node and its neighbors
        lines.append((" ADJACENCY MAP", curses.A_BOLD | curses.A_UNDERLINE))

        # Sort nodes by number of connections (hubs first)
        sorted_nodes = sorted(adjacency.keys(),
                              key=lambda n: len(adjacency[n]),
                              reverse=True)

        for nid in sorted_nodes:
            neighbors = adjacency[nid]
            name = node_names.get(nid, str(nid)[:8])
            conn_count = len(neighbors)
            lines.append(("", 0))
            lines.append((f"  [{name}] ({conn_count} links)",
                           curses.A_BOLD))

            for tgt_id, snr, quality in sorted(neighbors,
                                                key=lambda x: x[1],
                                                reverse=True):
                tgt_name = node_names.get(tgt_id, str(tgt_id)[:8])
                # Pick link style based on quality
                if quality == "excellent":
                    link_char = "==="
                    la = curses.color_pair(CP_HEALTH_EXCELLENT)
                elif quality == "good":
                    link_char = "---"
                    la = curses.color_pair(CP_HEALTH_GOOD)
                elif quality == "marginal":
                    link_char = "..."
                    la = curses.color_pair(CP_HEALTH_FAIR)
                elif quality in ("poor", "bad"):
                    link_char = "~~~"
                    la = curses.color_pair(CP_HEALTH_POOR)
                else:
                    link_char = "???"
                    la = curses.A_DIM

                snr_str = f"{snr:>5.1f}dB" if snr else "   --  "
                lines.append((f"    {link_char}{link_char} {tgt_name:<14} "
                              f"SNR:{snr_str}", la))

        lines.append(("", 0))

        # Link table (sorted by SNR)
        lines.append((" ALL LINKS (by SNR)", curses.A_BOLD | curses.A_UNDERLINE))
        link_hdr = f"  {'Source':<14}{'Target':<14}{'SNR':>8}  {'Quality':<12}"
        lines.append((link_hdr, curses.color_pair(CP_HIGHLIGHT)))

        sorted_links = sorted(links, key=lambda l: l.get("snr", 0),
                              reverse=True)
        for lk in sorted_links:
            src_name = node_names.get(lk["source"], lk["source"][:10])
            tgt_name = node_names.get(lk["target"], lk["target"][:10])
            snr = lk.get("snr", 0)
            quality = lk.get("quality", "?")
            snr_str = f"{snr:>7.1f}" if snr else "     --"
            la = _quality_color(quality)
            lines.append((f"  {src_name:<14}{tgt_name:<14}{snr_str}  "
                          f"{quality:<12}", la))

    # Render scrolled
    visible = lines[scroll:scroll + height]
    for i, (text, attr) in enumerate(visible):
        safe_addstr(win, top + i, 0, text, attr, cols)
