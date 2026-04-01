#!/usr/bin/env python3
"""Check meshforge-maps API response for node counts per network."""
import json
import urllib.request

base = "http://localhost:8808"
try:
    with urllib.request.urlopen(base + "/api/nodes/geojson", timeout=10) as r:
        data = json.loads(r.read())
    props = data.get("properties", {})
    print("Sources:", props.get("sources", {}))
    print("Total nodes:", props.get("total_nodes", 0))
    print("Enabled:", props.get("enabled_sources", []))

    # Count by network from actual features
    by_net = {}
    for f in data.get("features", []):
        net = f.get("properties", {}).get("network", "unknown")
        by_net[net] = by_net.get(net, 0) + 1
    print("Features by network:", by_net)
except Exception as e:
    print("Error:", e)

try:
    with urllib.request.urlopen(base + "/api/mqtt/stats", timeout=5) as r:
        mq = json.loads(r.read())
    print("MQTT store:", mq.get("node_count", 0), "nodes,",
          mq.get("messages_received", 0), "messages")
except Exception as e:
    print("MQTT stats error:", e)
