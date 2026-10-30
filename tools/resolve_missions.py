#!/usr/bin/env python3
"""Match every mission place in data/districts/*.json against the downloaded OSM data,
put it on a drivable road, route between the places and set realistic time limits.

Each place in a mission ("start", "pickup", "dropoff", "target") lists the names it is
known by:

    "target": {"names": ["The Dubai Mall", "Dubai Mall"], "categories": ["shop=mall"]}

For each place this script:

1. Looks the names up in data/osm/<id>.json (POIs, fuel stations, named buildings).
   Only a name match counts: a place that is not in the data is an error and the
   mission is reported as unplayable (the script exits non-zero). There is no
   "nearest place of the same kind" fallback, so the dialogue always talks about
   the place the marker is on.
2. Snaps it to the nearest drivable road that is part of the main road network
   (not a motorway, slip road, bridge, tunnel or private road), so the marker is
   always on a road and never inside a building.
3. Routes along the road network (one-way streets respected, travel time from the
   OSM speed limits, or the default for the road type where OSM has none).

The result is written into the mission:

    "target": {..., "resolved": {"osm_id", "name", "category", "lat", "lon",
                                 "access": {"lat", "lon", "road", "highway", "snap_m"}}}
    "route": {"from": "start", "to": "target", "distance_m", "free_flow_s", "signals",
              "roads": [names], "time_limit_rule": "..."}
    "time_limit": 95          # timed and delivery missions only

Time limit = free-flow time at the legal limit x margin + a fixed allowance, rounded
up to 5 s (see TIME_RULES). Driving at exactly the posted limit the whole way and
cornering at speed is not realistic; the margin covers turns, roundabouts and
pulling away from the start.

It also checks the district's spawn point: it moves it onto the nearest suitable real
road (main network, not a motorway, bridge or tunnel) and turns it to face along
the road in a legal direction.

A report is written to data/place_verification.md.

Usage:
    python3 tools/resolve_missions.py
    python3 tools/resolve_missions.py --list-pois downtown [--grep mall]
"""

import argparse
import heapq
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_osm import directed_adjacency, strongly_connected  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISTRICT_DIR = os.path.join(ROOT, "data", "districts")
OSM_DIR = os.path.join(ROOT, "data", "osm")
REPORT = os.path.join(ROOT, "data", "place_verification.md")
ROLES = ("start", "pickup", "dropoff", "target")

# Roads a car can stop on to pick someone up or drop something off.
NO_STOP_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link"}
# Roads a pedestrian cannot cross between a place and its road access point.
BARRIER_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link"}
MAX_SNAP_M = 300.0
SPAWN_HIGHWAYS = ("residential", "tertiary", "secondary", "unclassified", "primary")

# type -> (margin on free-flow time, fixed seconds). None = no time limit.
TIME_RULES = {
    "timed": (1.35, 15.0),
    "delivery": (1.6, 25.0),
    "drive_to": None,
    "speed_limit": None,
}


def norm(s):
    s = (s or "").lower().replace("’", "'").replace("'", "")
    s = re.sub(r"[^a-z0-9؀-ۿ]+", " ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    return s


class Proj:
    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = 111320.0 * math.cos(math.radians(lat0))
        self.ky = 110540.0

    def xy(self, lat, lon):
        return ((lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky)

    def latlon(self, x, y):
        return (self.lat0 + y / self.ky, self.lon0 + x / self.kx)


def dist_m(a_lat, a_lon, b_lat, b_lon):
    x = (b_lon - a_lon) * 111320.0 * math.cos(math.radians((a_lat + b_lat) / 2))
    y = (b_lat - a_lat) * 110540.0
    return math.hypot(x, y)


def osm_path(district):
    rel = district.get("osm_file", "res://data/osm/%s.json" % district["id"])
    return os.path.join(ROOT, rel.replace("res://", "", 1))


def load_places(osm):
    """All named things the missions may point at: POIs, fuel stations, named buildings."""
    places = []
    for p in osm.get("pois", []):
        places.append(dict(p))
    for f in osm.get("fuel", []):
        label = f.get("name") or f.get("brand")
        if label:
            places.append({"id": f["id"], "name": label, "name_en": f.get("brand", ""),
                           "category": "amenity=fuel", "lat": f["lat"], "lon": f["lon"]})
    for b in osm.get("buildings", []):
        if b.get("name"):
            pts = b["points"]
            places.append({"id": b["id"], "name": b["name"], "name_en": "",
                           "category": "building=%s" % b.get("kind", "yes"),
                           "lat": sum(p[0] for p in pts) / len(pts),
                           "lon": sum(p[1] for p in pts) / len(pts)})
    for p in places:
        p["_names"] = {n for n in (norm(p.get("name")), norm(p.get("name_en"))) if n}
    return places


def cat_rank(place, cats):
    if not cats:
        return 0
    return 0 if place["category"] in cats else 1


def find_by_name(places, ref):
    cats = ref.get("categories", [])
    for wanted in ref.get("names", []):
        w = norm(wanted)
        if not w:
            continue
        exact = [p for p in places if w in p["_names"]]
        if exact:
            return sorted(exact, key=lambda p: (cat_rank(p, cats), p["id"]))[0]
        pat = re.compile(r"(^| )%s( |$)" % re.escape(w))
        partial = [p for p in places if any(pat.search(n) for n in p["_names"])]
        if partial:
            return sorted(partial, key=lambda p: (cat_rank(p, cats), len(p["name"]), p["id"]))[0]
    return None


# ---------------------------------------------------------------------------
# Road graph


class RoadGraph:
    """Directed graph over OSM road nodes. Edge cost is travel time at the speed limit."""

    def __init__(self, osm):
        o = osm["origin"]
        self.proj = Proj(o["lat"], o["lon"])
        self.pos = {}       # node id -> (x, y)
        self.adj = {}       # node id -> [(node id, seconds, metres, road index)]
        self.roads = osm["roads"]
        self.segs = []      # (road index, i) for snapping
        self.signal_nodes = set()
        for s in osm.get("signals", []):
            if s["kind"] == "traffic_signals":
                self.signal_nodes.add(int(s["id"].split("/")[1]))
        # Only segments inside the main strongly connected network: from any of them you
        # can reach every other one legally (no one-way dead ends, no exit-only stubs).
        main = strongly_connected(directed_adjacency(
            [r for r in self.roads if r.get("connected", True)]))
        for ri, r in enumerate(self.roads):
            if not r.get("connected", True) or r.get("private"):
                continue
            v = max(float(r["maxspeed_kmh"]), 5.0) / 3.6
            nodes, pts = r["nodes"], r["points"]
            for i in range(len(nodes) - 1):
                a, b = nodes[i], nodes[i + 1]
                if a not in main or b not in main:
                    continue
                pa, pb = self.proj.xy(*pts[i]), self.proj.xy(*pts[i + 1])
                self.pos[a], self.pos[b] = pa, pb
                length = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
                if length < 0.01:
                    continue
                self.adj.setdefault(a, []).append((b, length / v, length, ri))
                if not r["oneway"]:
                    self.adj.setdefault(b, []).append((a, length / v, length, ri))
                self.segs.append((ri, i))

    def _barriers(self):
        """Motorway and trunk carriageways at ground level: nobody walks across them, so a
        place on one side is not served by a road on the other side."""
        if not hasattr(self, "_barrier_segs"):
            self._barrier_segs = []
            for r in self.roads:
                if r["highway"] in BARRIER_HIGHWAYS and not r.get("bridge") and not r.get("tunnel"):
                    pts = [self.proj.xy(*p) for p in r["points"]]
                    self._barrier_segs += list(zip(pts, pts[1:]))
        return self._barrier_segs

    def crosses_barrier(self, a, b):
        lo = (min(a[0], b[0]), min(a[1], b[1]))
        hi = (max(a[0], b[0]), max(a[1], b[1]))
        for p, q in self._barriers():
            if max(p[0], q[0]) < lo[0] or min(p[0], q[0]) > hi[0] or \
                    max(p[1], q[1]) < lo[1] or min(p[1], q[1]) > hi[1]:
                continue
            if _segments_cross(a, b, p, q):
                return True
        return False

    def snap(self, lat, lon, ok=None, max_m=MAX_SNAP_M):
        """Nearest point on an allowed road segment that is on the same side of any
        motorway/trunk as the place: dict or None."""
        px, py = self.proj.xy(lat, lon)
        cands = []
        for ri, i in self.segs:
            r = self.roads[ri]
            if ok and not ok(r):
                continue
            ax, ay = self.proj.xy(*r["points"][i])
            bx, by = self.proj.xy(*r["points"][i + 1])
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy
            t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
            qx, qy = ax + t * dx, ay + t * dy
            d = math.hypot(px - qx, py - qy)
            if d <= max_m:
                cands.append((d, ri, i, t, qx, qy, dx, dy, l2))
        cands.sort()
        best = None
        for d, ri, i, t, qx, qy, dx, dy, l2 in cands:
            if self.crosses_barrier((px, py), (qx, qy)):
                continue
            best = {"snap_m": d, "road_index": ri, "seg": i, "t": t, "xy": (qx, qy),
                    "dir": (dx / math.sqrt(l2), dy / math.sqrt(l2)) if l2 else (0.0, 1.0)}
            break
        if best is None:
            return None
        r = self.roads[best["road_index"]]
        best["lat"], best["lon"] = self.proj.latlon(*best["xy"])
        best["road"] = r["name"] or "(unnamed %s)" % r["highway"]
        best["highway"] = r["highway"]
        return best

    def _seg_info(self, s):
        r = self.roads[s["road_index"]]
        a, b = r["nodes"][s["seg"]], r["nodes"][s["seg"] + 1]
        pa, pb = self.pos[a], self.pos[b]
        length = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
        v = max(float(r["maxspeed_kmh"]), 5.0) / 3.6
        return a, b, length, v, r["oneway"]

    def route(self, src, dst):
        """Fastest legal route between two snapped points.
        Returns {distance_m, free_flow_s, signals, roads} or None."""
        a, b, length, v, oneway = self._seg_info(src)
        t = src["t"]
        # Start: leave the source segment forwards (to b) or, if two-way, backwards (to a).
        starts = [(b, (1 - t) * length / v, (1 - t) * length)]
        if not oneway:
            starts.append((a, t * length / v, t * length))
        da, db, dlen, dv, doneway = self._seg_info(dst)
        dt = dst["t"]
        # Finish: enter the destination segment from its start (a) or, if two-way, its end (b).
        ends = {da: (dt * dlen / dv, dt * dlen)}
        if not doneway:
            ends[db] = min(ends.get(db, (1e18, 0)), ((1 - dt) * dlen / dv, (1 - dt) * dlen))
        best = None
        if src["road_index"] == dst["road_index"] and src["seg"] == dst["seg"]:
            if dt >= t or not oneway:
                d = abs(dt - t) * length
                best = (d / v, d, [src["road_index"]], [])
        dist = {}
        heap = []
        for n, c, l in starts:
            if c < dist.get(n, (1e18,))[0]:
                dist[n] = (c, l, None, None)
                heapq.heappush(heap, (c, n))
        done = set()
        while heap:
            c, n = heapq.heappop(heap)
            if n in done:
                continue
            done.add(n)
            if best is not None and c >= best[0]:
                break
            if n in ends:
                ec, el = ends[n]
                total = c + ec
                if best is None or total < best[0]:
                    path_nodes, path_roads = [], [dst["road_index"]]
                    k = n
                    while k is not None:
                        path_nodes.append(k)
                        _, _, prev, road = dist[k]
                        if road is not None:
                            path_roads.append(road)
                        k = prev
                    path_roads.append(src["road_index"])
                    best = (total, dist[n][1] + el, path_roads[::-1], path_nodes[::-1])
            for m, ec, el, ri in self.adj.get(n, ()):
                nc = c + ec
                if nc < dist.get(m, (1e18,))[0]:
                    dist[m] = (nc, dist[n][1] + el, n, ri)
                    heapq.heappush(heap, (nc, m))
        if best is None:
            return None
        names = []
        for ri in best[2]:
            nm = self.roads[ri]["name"]
            if nm and (not names or names[-1] != nm):
                names.append(nm)
        return {"distance_m": round(best[1]), "free_flow_s": round(best[0], 1),
                "signals": sum(1 for n in best[3] if n in self.signal_nodes),
                "roads": names}


def _segments_cross(p1, p2, p3, p4):
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def stoppable(r):
    return (r["highway"] not in NO_STOP_HIGHWAYS and not r.get("bridge") and not r.get("tunnel")
            and not r.get("private") and r.get("connected", True))


def ceil5(x):
    return int(math.ceil(x / 5.0) * 5)


def resolve_place(ref, places, graph):
    hit = find_by_name(places, ref)
    if hit is None:
        ref.pop("resolved", None)
        return None, "not found in OSM data"
    snap = graph.snap(hit["lat"], hit["lon"], stoppable)
    if snap is None:
        ref.pop("resolved", None)
        return None, "%s found but no drivable road within %d m on its side of the motorway" % (
            hit["name"], MAX_SNAP_M)
    ref.pop("allow_fallback", None)
    ref["resolved"] = {
        "osm_id": hit["id"], "name": hit.get("name_en") or hit["name"],
        "category": hit["category"], "lat": round(hit["lat"], 6), "lon": round(hit["lon"], 6),
        "access": {"lat": round(snap["lat"], 6), "lon": round(snap["lon"], 6),
                   "road": snap["road"], "highway": snap["highway"],
                   "snap_m": round(snap["snap_m"], 1)},
    }
    return snap, None


def place_spawn(district, graph):
    """Put the spawn on a real road near the requested point, facing a legal direction."""
    s = district["spawn"]
    want = (s.get("hint_lat", s["lat"]), s.get("hint_lon", s["lon"]))

    def ok(r, named):
        return (stoppable(r) and r["highway"] in SPAWN_HIGHWAYS and not r.get("roundabout")
                and (r["name"] or not named))
    best = None
    for named in (True, False):  # a named street reads better on the HUD
        for limit in (300.0, 800.0, 2000.0):
            best = graph.snap(want[0], want[1], lambda r: ok(r, named), max_m=limit)
            if best:
                break
        if best:
            break
    if best is None:
        return None
    r = graph.roads[best["road_index"]]
    # Stand in the middle of the segment, away from junctions.
    ax, ay = graph.proj.xy(*r["points"][best["seg"]])
    bx, by = graph.proj.xy(*r["points"][best["seg"] + 1])
    seg_len = math.hypot(bx - ax, by - ay)
    t = 0.5 if seg_len > 20 else best["t"]
    lat, lon = graph.proj.latlon(ax + (bx - ax) * t, ay + (by - ay) * t)
    dx, dy = best["dir"]
    if not r["oneway"] and "heading_deg" in s:
        h = math.radians(float(s.get("hint_heading_deg", s["heading_deg"])))
        if dx * math.sin(h) + dy * math.cos(h) < 0:
            dx, dy = -dx, -dy
    heading = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0  # 0 = north, 90 = east
    district["spawn"] = {
        "lat": round(lat, 6), "lon": round(lon, 6), "heading_deg": round(heading, 1),
        "road": r["name"] or "(unnamed %s)" % r["highway"], "osm_id": r["id"],
        "hint_lat": want[0], "hint_lon": want[1],
    }
    return district["spawn"]


def resolve_district(district, report):
    path = osm_path(district)
    lines = ["## %s (`%s`)" % (district["name"], district["id"]), ""]
    problems = []
    if not os.path.exists(path):
        lines += ["**No OSM data** (`%s` missing). Run `python3 tools/fetch_osm.py %s` first." % (
            os.path.relpath(path, ROOT), district["id"]), ""]
        for m in district["missions"]:
            for role in ROLES:
                if role in m:
                    m[role].pop("resolved", None)
            problems.append((m["id"], "-", "no OSM data"))
        report.append("\n".join(lines))
        return problems, 0

    with open(path, encoding="utf-8") as f:
        osm = json.load(f)
    places = load_places(osm)
    graph = RoadGraph(osm)
    spawn = place_spawn(district, graph)
    if spawn:
        lines.append("Spawn: %.6f, %.6f on **%s** (`%s`), heading %.0f°." % (
            spawn["lat"], spawn["lon"], spawn["road"], spawn["osm_id"], spawn["heading_deg"]))
    else:
        problems.append(("spawn", "-", "no suitable road near the spawn point"))
    lines += ["", "| Mission | Role | OSM place | OSM id | Place lat, lon | Road access (snap) |",
              "|---|---|---|---|---|---|"]
    ok_missions = 0
    routes = []
    for m in district["missions"]:
        m_ok = True
        snaps = {}
        for role in ROLES:
            ref = m.get(role)
            if ref is None:
                continue
            snap, err = resolve_place(ref, places, graph)
            wanted = " / ".join(ref.get("names", []))
            if err:
                m_ok = False
                problems.append((m["id"], role, "%s: %s" % (wanted, err)))
                lines.append("| %s | %s | **NOT FOUND**: %s | | | %s |" % (m["id"], role, wanted, err))
                continue
            snaps[role] = snap
            res = ref["resolved"]
            lines.append("| %s | %s | %s | `%s` | %.6f, %.6f | %s, %.0f m |" % (
                m["id"], role, res["name"], res["osm_id"], res["lat"], res["lon"],
                res["access"]["road"], res["access"]["snap_m"]))
        legs = (["pickup", "dropoff"] if m["type"] == "delivery" else ["start", "target"])
        missing = [r for r in legs if r not in m]
        if missing:
            m_ok = False
            problems.append((m["id"], "-", "missing %s" % ", ".join(missing)))
        m.pop("route", None)
        if m_ok:
            route = graph.route(snaps[legs[0]], snaps[legs[1]])
            if route is None:
                m_ok = False
                problems.append((m["id"], "-", "no legal route %s -> %s" % tuple(legs)))
            else:
                rule = TIME_RULES.get(m["type"])
                route = {"from": legs[0], "to": legs[1], **route}
                if rule:
                    margin, extra = rule
                    m["time_limit"] = ceil5(route["free_flow_s"] * margin + extra)
                    route["time_limit_rule"] = "ceil5(%.1f s x %.2f + %d s)" % (
                        route["free_flow_s"], margin, extra)
                else:
                    m.pop("time_limit", None)
                m["route"] = route
                routes.append((m, route))
        ok_missions += 1 if m_ok else 0
    lines += ["", "### Routes and time limits", "",
              "| Mission | Type | Leg | Distance | Free-flow at limits | Signals | Time limit | Via |",
              "|---|---|---|---|---|---|---|---|"]
    for m, r in routes:
        lines.append("| %s | %s | %s -> %s | %.2f km | %d s | %d | %s | %s |" % (
            m["id"], m["type"], r["from"], r["to"], r["distance_m"] / 1000.0, r["free_flow_s"],
            r["signals"], ("%d s" % m["time_limit"]) if "time_limit" in m else "-",
            ", ".join(r["roads"][:6]) + (" ..." if len(r["roads"]) > 6 else "")))
    lines.append("")
    report.append("\n".join(lines))
    return problems, ok_missions


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list-pois", metavar="DISTRICT", help="print the named places of a district")
    ap.add_argument("--grep", help="filter --list-pois by text")
    args = ap.parse_args()

    files = sorted(f for f in os.listdir(DISTRICT_DIR) if f.endswith(".json"))
    districts = []
    for fn in files:
        with open(os.path.join(DISTRICT_DIR, fn), encoding="utf-8") as f:
            districts.append((fn, json.load(f)))

    if args.list_pois:
        d = next((d for _, d in districts if d["id"] == args.list_pois), None)
        if d is None or not os.path.exists(osm_path(d)):
            sys.exit("no OSM data for %s" % args.list_pois)
        with open(osm_path(d), encoding="utf-8") as f:
            places = load_places(json.load(f))
        for p in sorted(places, key=lambda p: (p["category"], p["name"])):
            line = "%-28s %-45s %-16s %.6f, %.6f %s" % (
                p["category"], p["name"], p["id"], p["lat"], p["lon"], p.get("name_en", ""))
            if not args.grep or args.grep.lower() in line.lower():
                print(line)
        return

    report = ["# Mission place verification", "",
              "Generated by `tools/resolve_missions.py` from the OSM files in `data/osm/`. "
              "Every place below was matched by name to a real OpenStreetMap element; "
              "coordinates come from OSM. *Road access* is the point on the nearest drivable "
              "road where the marker goes. Time limits come from the fastest legal route on "
              "the OSM road network at the OSM speed limits.", ""]
    all_problems = []
    total_ok = 0
    total = 0
    for fn, d in districts:
        problems, ok = resolve_district(d, report)
        total_ok += ok
        total += len(d["missions"])
        all_problems += [(d["id"],) + p for p in problems]
        with open(os.path.join(DISTRICT_DIR, fn), "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print("%-12s %d/%d missions playable" % (d["id"], ok, len(d["missions"])))

    report += ["## Problems", ""]
    if all_problems:
        report += ["| District | Mission | Role | Problem |", "|---|---|---|---|"]
        report += ["| %s | %s | %s | %s |" % p for p in all_problems]
    else:
        report.append("None: every place was found by name, is on a drivable road and is reachable.")
    report.append("")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("%d/%d missions playable; %d problem(s). Report: %s" % (
        total_ok, total, len(all_problems), os.path.relpath(REPORT, ROOT)))
    for p in all_problems:
        print("  %s %s %s: %s" % p)
    if all_problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
