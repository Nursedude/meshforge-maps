#!/usr/bin/env python3
"""Field drill for the GET read-surface tiering. Run against a LIVE server.

Asks the deployed process the one question the unit tests cannot: does the
running box actually refuse an anonymous introspective GET, and still answer
the display surface the NOC map paints with?

Every check carries a CONTROL. A drill that can only answer one way proves
nothing -- so each refusal is paired with the same request bearing the key,
and each projection check is paired with the admin view of the same endpoint.

⚠️ The guard must EXIST before it can be defeated. On a box with no api_key
configured, every endpoint is open BY DESIGN and all of these checks would
"pass" for the wrong reason. That case exits 2 (UNKNOWN), never 0.

Usage:
    read_surface_drill.py --base http://127.0.0.1:8808 --key "$(cat keyfile)"

Exit: 0 verified · 1 a check failed · 2 UNKNOWN (cannot determine)
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 10

# Introspective GETs: must refuse anonymously, must answer with the key.
GATED = [
    "/api/dependencies",
    "/api/config-drift",
    "/api/config-drift/summary",
    "/api/perf",
    "/api/proxy/stats",
    "/api/core-health",
    "/api/export/nodes",
    "/api/export/alerts",
    "/api/export/analytics/growth",
    "/api/export/analytics/activity",
    "/api/export/analytics/ranking",
]

# The product. Must answer WITHOUT a key or the NOC display is broken.
DISPLAY = [
    "/api/nodes/geojson",
    "/api/status",
    "/api/health",
    "/api/topology/geojson",
    "/api/alerts/active",
    "/api/analytics/growth",
    "/api/heatmap",
]

# Keys that described the deployment and used to be served anonymously.
MUST_NOT_LEAK = [
    "mqtt_broker", "mqtt_topic", "mqtt_brokers", "aredn_node_ips",
    "trusted_proxies", "meshtasticd_host", "meshtasticd_port", "rch_host",
    "mesh_client_path", "ws_allowed_origins", "cors_allowed_origin",
    "http_host", "http_port", "rate_limit_per_minute", "owned_node_ids",
]


def fetch(base, path, key=None):
    """Return (status, body_text). Status None means the transport failed."""
    headers = {"Accept": "application/json"}
    if key:
        headers["X-MeshForge-Key"] = key
    req = urllib.request.Request(base.rstrip("/") + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, OSError) as e:
        print(f"  transport error on {path}: {e}")
        return None, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8808")
    ap.add_argument("--key", required=True, help="admin API key for the controls")
    args = ap.parse_args()

    # -- precondition: the guard must be armed --------------------------------
    status, body = fetch(args.base, "/api/auth/check")
    if status != 200:
        print(f"UNKNOWN: /api/auth/check answered {status} -- server unreachable?")
        return 2
    try:
        auth = json.loads(body)
    except ValueError:
        print("UNKNOWN: /api/auth/check returned non-JSON")
        return 2
    if not auth.get("admin_required"):
        print("UNKNOWN: no api_key configured on this box -- every endpoint is "
              "open BY DESIGN, so these checks would pass for the wrong reason.")
        return 2
    st_ctl, ctl_body = fetch(args.base, "/api/auth/check", key=args.key)
    ctl = json.loads(ctl_body or "{}")
    if not ctl.get("authenticated"):
        print("UNKNOWN: the --key supplied is not this box's admin key; the "
              "controls below would fail for the wrong reason.")
        return 2
    print(f"guard armed: admin_required=True, supplied key authenticates "
          f"(control /api/auth/check -> {st_ctl})")

    failures = []

    # -- 1. gated GETs refuse anonymously, answer with the key ----------------
    print("\n[1] introspective GETs -- refuse anonymous, answer admin")
    for path in GATED:
        anon, _ = fetch(args.base, path)
        adm, _ = fetch(args.base, path, key=args.key)
        ok = anon == 401 and adm not in (401, None)
        print(f"  {'PASS' if ok else 'FAIL'}  {path:<34} anon={anon} admin={adm}")
        if not ok:
            failures.append(f"{path}: anon={anon} (want 401), admin={adm} (want != 401)")

    # -- 2. display surface stays open ----------------------------------------
    print("\n[2] display GETs -- must answer WITHOUT a key (the NOC map)")
    for path in DISPLAY:
        anon, _ = fetch(args.base, path)
        ok = anon == 200
        print(f"  {'PASS' if ok else 'FAIL'}  {path:<34} anon={anon}")
        if not ok:
            failures.append(f"{path}: anon={anon} (want 200) -- display broke")

    # -- 3. /api/config projection, with the admin view as control ------------
    print("\n[3] /api/config -- display projection vs admin view")
    anon_st, anon_body = fetch(args.base, "/api/config")
    adm_st, adm_body = fetch(args.base, "/api/config", key=args.key)
    if anon_st != 200 or adm_st != 200:
        failures.append(f"/api/config: anon={anon_st} admin={adm_st} (want 200/200)")
        print(f"  FAIL  unexpected status anon={anon_st} admin={adm_st}")
    else:
        anon_cfg = json.loads(anon_body)
        adm_cfg = json.loads(adm_body)
        leaked = [k for k in MUST_NOT_LEAK if k in anon_cfg]
        # CONTROL: the admin view must actually CONTAIN them, or "absent from
        # the anonymous view" would be meaningless -- an empty config would
        # satisfy the check while telling us nothing.
        present_for_admin = [k for k in MUST_NOT_LEAK if k in adm_cfg]
        print(f"  {'PASS' if not leaked else 'FAIL'}  anonymous view: "
              f"{len(anon_cfg)} keys, infra keys leaked: {leaked or 'none'}")
        print(f"  {'PASS' if present_for_admin else 'FAIL'}  control: admin view "
              f"has {len(adm_cfg)} keys, {len(present_for_admin)} infra keys present")
        if leaked:
            failures.append(f"/api/config served infra keys anonymously: {leaked}")
        if not present_for_admin:
            failures.append(
                "/api/config control failed: admin view has no infra keys either, "
                "so the anonymous check proved nothing")
        # Credentials must never appear, in either view.
        for view, cfg in (("anonymous", anon_cfg), ("admin", adm_cfg)):
            for entry in (cfg.get("mqtt_brokers") or []):
                if isinstance(entry, dict):
                    for secret in ("username", "password"):
                        val = entry.get(secret)
                        if val not in (None, "***"):
                            failures.append(
                                f"/api/config[{view}] mqtt_brokers leaked {secret}")
                            print(f"  FAIL  {view} view leaked mqtt_brokers.{secret}")
            if cfg.get("api_key") not in (None, "***"):
                failures.append(f"/api/config[{view}] echoed api_key in clear")
                print(f"  FAIL  {view} view echoed api_key")

    # -- 4. an admin fetch must not warm a cache the next anon caller reads --
    # This server uses several response caches (_html_cache, the aggregator's
    # pre-serialized JSON). If /api/config ever gained one keyed on path alone,
    # the projection would leak the full view to whoever asked next. Order
    # matters: admin FIRST, anonymous SECOND -- the reverse cannot detect it.
    print("\n[4] cache-order -- admin fetch must not poison the anonymous view")
    fetch(args.base, "/api/config", key=args.key)          # warm as admin
    st_after, body_after = fetch(args.base, "/api/config")  # then ask anonymously
    if st_after != 200:
        failures.append(f"/api/config anon-after-admin: status {st_after}")
        print(f"  FAIL  anonymous fetch after admin answered {st_after}")
    else:
        after = json.loads(body_after)
        bled = [k for k in MUST_NOT_LEAK if k in after]
        print(f"  {'PASS' if not bled else 'FAIL'}  anon-after-admin: "
              f"{len(after)} keys, bled through: {bled or 'none'}")
        if bled:
            failures.append(
                f"/api/config leaked the admin view to the next anonymous "
                f"caller (response cache?): {bled}")

    # -- 5. HEAD must not reach a gated handler -----------------------------
    # There is no do_HEAD override, so HEAD falls through to the static file
    # handler. Assert that rather than trusting it: a future do_HEAD that
    # dispatched through _ROUTE_TABLE would bypass the gate entirely.
    print("\n[5] HEAD on a gated path must not be served by the API")
    req = urllib.request.Request(
        args.base.rstrip("/") + "/api/dependencies", method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            head_status = resp.status
    except urllib.error.HTTPError as e:
        head_status = e.code
    except (urllib.error.URLError, OSError) as e:
        head_status = None
        print(f"  transport error: {e}")
    ok_head = head_status in (401, 404)
    print(f"  {'PASS' if ok_head else 'FAIL'}  HEAD /api/dependencies -> "
          f"{head_status} (want 401 refused or 404 static miss)")
    if not ok_head:
        failures.append(
            f"HEAD /api/dependencies answered {head_status} -- a HEAD path "
            f"reaches the gated handler without passing the gate")

    print()
    if failures:
        print(f"DRILL FAILED -- {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("DRILL PASSED -- gate refuses anonymous introspection, display surface "
          "open, config projected, no credentials in either view.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
