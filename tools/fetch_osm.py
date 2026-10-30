#!/usr/bin/env python3
"""Download OpenStreetMap data for each district and write a compact JSON file.

The district list and bounding boxes are read from data/districts/*.json, so a
new district only needs a new data file. Output goes to data/osm/<id>.json:

    {
      "district": "downtown",
      "bbox": {"south": .., "west": .., "north": .., "east": ..},
      "origin": {"lat": .., "lon": ..},          # bbox centre, used for projection
      "roads":     [{"id", "name", "highway", "maxspeed_kmh", "lanes", "oneway", "points": [[lat, lon], ..]}],
      "buildings": [{"id", "name", "kind", "min_height", "height", "levels", "points": [[lat, lon], ..]}],
      "fuel":      [{"id", "name", "brand", "lat", "lon"}],
      "pois":      [{"id", "name", "name_en", "category", "lat", "lon"}]
    }

Usage:
    python3 tools/fetch_osm.py                  # all districts
    python3 tools/fetch_osm.py downtown mirdif  # selected districts
    python3 tools/fetch_osm.py --raw-only       # just download raw Overpass JSON
    python3 tools/fetch_osm.py --from-raw       # rebuild from data/osm_raw/ without downloading

No third-party packages are needed.
"""

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISTRICT_DIR = os.path.join(ROOT, "data", "districts")
OUT_DIR = os.path.join(ROOT, "data", "osm")
RAW_DIR = os.path.join(ROOT, "data", "osm_raw")

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

DRIVABLE = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
    "residential", "living_street", "service", "road",
}

# (tag key, accepted values or None for any value). Order decides the category label.
POI_TAGS = [
    ("shop", {"mall", "department_store", "supermarket"}),
    ("tourism", None),
    ("leisure", {"park", "garden", "stadium", "sports_centre", "water_park",
                 "beach_resort", "playground", "pitch", "golf_course", "marina"}),
    ("amenity", None),
    ("railway", {"station", "halt", "tram_stop"}),
    ("public_transport", {"station"}),
    ("natural", {"beach"}),
    ("historic", None),
    ("building", {"mosque", "church", "hotel", "hospital", "school", "retail", "commercial"}),
    ("office", None),
]

# Clip geometry to the bbox expanded by this many metres.
CLIP_MARGIN_M = 150.0


def load_districts(ids):
    districts = []
    for fn in sorted(os.listdir(DISTRICT_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(DISTRICT_DIR, fn), encoding="utf-8") as f:
            d = json.load(f)
        if ids and d["id"] not in ids:
            continue
        districts.append(d)
    return districts


def build_query(b):
    bbox = "%f,%f,%f,%f" % (b["south"], b["west"], b["north"], b["east"])
    poi_lines = []
    for key, values in POI_TAGS:
        if values is None:
            poi_lines.append('  nwr["name"]["%s"];' % key)
        else:
            poi_lines.append('  nwr["name"]["%s"~"^(%s)$"];' % (key, "|".join(sorted(values))))
    return "\n".join([
        "[out:json][timeout:240][bbox:%s];" % bbox,
        "(",
        '  way["highway"~"^(%s)$"];' % "|".join(sorted(DRIVABLE)),
        ")->.roads;",
        "(",
        '  way["building"];',
        '  relation["building"]["type"="multipolygon"];',
        ")->.buildings;",
        "(",
        '  nwr["amenity"="fuel"];',
        ")->.fuel;",
        'node["highway"~"^(traffic_signals|stop|give_way|mini_roundabout)$"]->.signals;',
        "(",
        *poi_lines,
        ")->.pois;",
        ".roads out body geom;",
        ".buildings out tags geom;",
        ".fuel out tags center;",
        ".signals out;",
        ".pois out tags center;",
    ])


def download(query, endpoints, rounds=4):
    """POST the query, cycling through the endpoints until one answers.
    Connection errors move straight to the next mirror; 429/504 and Overpass
    runtime errors (timeouts reported inside a 200 response) back off and retry."""
    data = urllib.parse.urlencode({"data": query}).encode()
    last_err = None
    for rnd in range(rounds):
        for url in endpoints:
            try:
                print("  POST %s (round %d)" % (url, rnd + 1), flush=True)
                req = urllib.request.Request(url, data=data, headers={
                    "User-Agent": "dubai-driving-game-osm-fetch/1.1",
                    "Content-Type": "application/x-www-form-urlencoded",
                })
                with urllib.request.urlopen(req, timeout=120) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                remark = raw.get("remark", "")
                if "runtime error" in remark or "Query timed out" in remark:
                    raise ValueError("Overpass: %s" % remark)
                return raw
            except (urllib.error.URLError, OSError, ValueError) as e:
                last_err = e
                print("    failed: %s" % e, flush=True)
                if isinstance(e, urllib.error.HTTPError) and e.code in (429, 504):
                    time.sleep(15)
        time.sleep(10 * (rnd + 1))
    raise RuntimeError("all Overpass endpoints failed: %s" % last_err)


OSM_API = "https://api.openstreetmap.org/api/0.6/map.json"


def download_osm_api(b, tiles=2):
    """Last resort when every Overpass mirror is down: the official OSM editing API.
    Same database, same elements; it just has no query language, so the bbox is
    fetched in tiles (each under the API's 50,000-node limit) and the elements are
    assembled into the shape Overpass returns (ways/relations with geometry, centers)."""
    nodes, ways, rels = {}, {}, {}
    stamp = ""

    def fetch(s, w, n, e, depth=0):
        nonlocal stamp
        url = "%s?bbox=%.6f,%.6f,%.6f,%.6f" % (OSM_API, w, s, e, n)
        for attempt in range(4):
            try:
                print("  GET %s" % url, flush=True)
                req = urllib.request.Request(url, headers={"User-Agent": "dubai-driving-game-osm-fetch/1.1"})
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as ex:
                if ex.code == 400 and depth < 3:  # too many nodes: split the tile
                    mlat, mlon = (s + n) / 2, (w + e) / 2
                    for q in ((s, w, mlat, mlon), (s, mlon, mlat, e), (mlat, w, n, mlon), (mlat, mlon, n, e)):
                        fetch(*q, depth=depth + 1)
                    return
                print("    failed: %s" % ex, flush=True)
                time.sleep(5 * 2 ** attempt)
            except (urllib.error.URLError, OSError, ValueError) as ex:
                print("    failed: %s" % ex, flush=True)
                time.sleep(5 * 2 ** attempt)
        else:
            raise RuntimeError("OSM API tile failed: %s" % url)
        for el in data.get("elements", []):
            stamp = max(stamp, el.get("timestamp", ""))
            {"node": nodes, "way": ways, "relation": rels}[el["type"]][el["id"]] = el
        time.sleep(1)

    dlat = (b["north"] - b["south"]) / tiles
    dlon = (b["east"] - b["west"]) / tiles
    for i in range(tiles):
        for j in range(tiles):
            fetch(b["south"] + i * dlat, b["west"] + j * dlon,
                  b["south"] + (i + 1) * dlat, b["west"] + (j + 1) * dlon)

    def geom(way):
        return [{"lat": nodes[n]["lat"], "lon": nodes[n]["lon"]} if n in nodes else None
                for n in way["nodes"]]

    def center(g):
        pts = [p for p in g if p]
        return {"lat": sum(p["lat"] for p in pts) / len(pts),
                "lon": sum(p["lon"] for p in pts) / len(pts)} if pts else None

    poi_match = lambda t: "name" in t and poi_category(t) is not None
    out = []
    for n in nodes.values():
        t = n.get("tags", {})
        if t.get("amenity") == "fuel" or t.get("highway") in ("traffic_signals", "stop", "give_way",
                                                                "mini_roundabout") or poi_match(t):
            out.append({"type": "node", "id": n["id"], "lat": n["lat"], "lon": n["lon"], "tags": t})
    for w in ways.values():
        t = w.get("tags", {})
        if not (t.get("highway") in DRIVABLE or "building" in t or t.get("amenity") == "fuel" or poi_match(t)):
            continue
        g = geom(w)
        el = {"type": "way", "id": w["id"], "nodes": w["nodes"], "tags": t}
        if t.get("highway") in DRIVABLE or "building" in t:
            el["geometry"] = g
        c = center(g)
        if c:
            el["center"] = c
        out.append(el)
    for r in rels.values():
        t = r.get("tags", {})
        is_bld = "building" in t and t.get("type") == "multipolygon"
        if not (is_bld or t.get("amenity") == "fuel" or poi_match(t)):
            continue
        members, pts = [], []
        for m in r.get("members", []):
            mm = dict(m)
            if m["type"] == "way" and m["ref"] in ways:
                mm["geometry"] = geom(ways[m["ref"]])
                pts += [p for p in mm["geometry"] if p]
            elif m["type"] == "node" and m["ref"] in nodes:
                pts.append({"lat": nodes[m["ref"]]["lat"], "lon": nodes[m["ref"]]["lon"]})
            members.append(mm)
        el = {"type": "relation", "id": r["id"], "members": members, "tags": t}
        c = center(pts)
        if c:
            el["center"] = c
        out.append(el)
    return {"version": 0.6, "generator": "OSM API v0.6 map (tiled, tools/fetch_osm.py)",
            "osm3s": {"timestamp_osm_base": stamp,
                      "copyright": "The data included in this document is from www.openstreetmap.org. "
                                   "The data is made available under ODbL."},
            "elements": out}


def parse_maxspeed(v):
    """'60', '60 km/h', '40 mph', '60;80' (first value). Anything else (e.g. 'AE:urban') -> None."""
    if not v:
        return None
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph)?\s*(?:;.*)?$", v)
    if not m:
        return None
    speed = float(m.group(1))
    if m.group(2) == "mph":
        speed *= 1.609344
    speed = int(round(speed))
    return speed if 5 <= speed <= 160 else None


def parse_float(v):
    if v is None:
        return None
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)", str(v))
    return float(m.group(1)) if m else None


def parse_height(v):
    """'45', '45 m', '45m', "150'" / '150 ft' (feet). Returns metres or None."""
    if v is None:
        return None
    s = str(v).strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(m|metres|meters|ft|feet|')?", s)
    if not m:
        return None
    h = float(m.group(1))
    if m.group(2) in ("ft", "feet", "'"):
        h *= 0.3048
    return h


def is_yes(v):
    return v not in (None, "", "no", "0", "false")


def element_id(el):
    return "%s/%d" % (el["type"], el["id"])


def element_center(el):
    if "lat" in el and "lon" in el:
        return el["lat"], el["lon"]
    if "center" in el:
        return el["center"]["lat"], el["center"]["lon"]
    if "geometry" in el and el["geometry"]:
        pts = [p for p in el["geometry"] if p]
        return (sum(p["lat"] for p in pts) / len(pts), sum(p["lon"] for p in pts) / len(pts))
    return None


def poi_category(tags):
    for key, values in POI_TAGS:
        v = tags.get(key)
        if v and (values is None or v in values):
            return "%s=%s" % (key, v)
    return None


class Clipper:
    def __init__(self, b):
        lat0 = (b["south"] + b["north"]) / 2.0
        dlat = CLIP_MARGIN_M / 110540.0
        dlon = CLIP_MARGIN_M / (111320.0 * math.cos(math.radians(lat0)))
        self.s, self.n = b["south"] - dlat, b["north"] + dlat
        self.w, self.e = b["west"] - dlon, b["east"] + dlon

    def inside(self, lat, lon):
        return self.s <= lat <= self.n and self.w <= lon <= self.e

    def split(self, pts):
        """Split a polyline into runs (lists of indices) that stay inside the clip box.
        One point outside the box is kept at each end so roads reach the edge."""
        runs, cur = [], []
        for i, (lat, lon) in enumerate(pts):
            if self.inside(lat, lon):
                if not cur and i > 0:
                    cur.append(i - 1)
                cur.append(i)
            elif cur:
                cur.append(i)
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        return [r for r in runs if len(r) >= 2]


def r6(v):
    return round(v, 6)


# ---------------------------------------------------------------------------
# Speed limits

# Used only where a road has no usable maxspeed tag. The four classes asked for are
# residential 40, arterial 60, major road 80 and motorway 120. Slip roads take the
# class below their parent; parking aisles and living streets are slower still.
SPEED_CLASS = {
    "motorway": ("motorway", 120),
    "motorway_link": ("major", 80),
    "trunk": ("major", 80), "primary": ("major", 80),
    "trunk_link": ("arterial", 60), "primary_link": ("arterial", 60),
    "secondary": ("arterial", 60), "secondary_link": ("arterial", 60),
    "tertiary": ("arterial", 60), "tertiary_link": ("arterial", 60),
    "residential": ("residential", 40), "unclassified": ("residential", 40),
    "road": ("residential", 40),
    "living_street": ("living_street", 20),
    "service": ("service", 25),
}


# ---------------------------------------------------------------------------
# Building heights

LEVEL_HEIGHT = 3.2
MAX_HEIGHT = 830.0          # Burj Khalifa is 828 m; anything taller is a tagging error
MIN_HEIGHT = 2.5
MIN_AREA_M2 = 4.0           # smaller polygons are mapping noise
MAX_AREA_M2 = 400000.0      # larger "buildings" are site outlines, not structures
MAX_SPAN_M = 1500.0

# Kinds whose height is typical wherever they are.
KIND_HEIGHT = {
    "house": 7.0, "detached": 7.0, "villa": 7.0, "semidetached_house": 7.0, "terrace": 7.0,
    "bungalow": 4.5, "garage": 3.0, "garages": 3.0, "carport": 3.0, "shed": 3.0, "hut": 3.0,
    "kiosk": 3.0, "roof": 4.5, "canopy": 5.0, "service": 4.0, "toilets": 3.5,
    "guardhouse": 3.0, "transformer_tower": 4.0, "mosque": 12.0, "school": 10.0,
    "construction": 8.0, "warehouse": 9.0, "industrial": 9.0, "retail": 8.0,
    "supermarket": 8.0, "parking": 12.0, "stadium": 20.0, "hospital": 18.0,
}
# Kinds that are low structures the car drives under or through: never solid.
NON_SOLID_KINDS = {"roof", "canopy", "carport", "bridge"}


def building_defaults(district):
    """District-appropriate fallback for generic kinds (yes, apartments, commercial...).
    Footprint size matters: in OSM-tagged Dubai data, footprints under ~400 m2 are
    villas and kiosks (median 3-4 m) while 500-3000 m2 footprints are the towers."""
    d = dict(district.get("building_defaults", {}))
    d.setdefault("generic_m", 9.0)
    d.setdefault("small_area_m2", 400.0)
    d.setdefault("small_m", 7.0)
    d.setdefault("large_area_m2", 3000.0)
    d.setdefault("large_m", d["generic_m"])
    d.setdefault("kinds", {})
    return d


class Projector:
    """Local metres around the bbox centre, for areas and lengths."""
    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = 111320.0 * math.cos(math.radians(lat0))
        self.ky = 110540.0

    def xy(self, lat, lon):
        return ((lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky)


def ring_area(xy):
    a = 0.0
    for i in range(len(xy)):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % len(xy)]
        a += x1 * y2 - x2 * y1
    return a * 0.5


def _seg_cross(p1, p2, p3, p4):
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 and d2 and d3 and d4


def self_intersects(xy):
    n = len(xy)
    if n > 400:
        return False  # very detailed outlines: trust them, the O(n^2) check is too slow
    for i in range(n):
        a, b = xy[i], xy[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _seg_cross(a, b, xy[j], xy[(j + 1) % n]):
                return True
    return False


def convex_hull(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def clean_ring(ring, proj):
    """Returns (cleaned [(lat, lon)] counter-clockwise seen from above, note) or (None, reason).
    Removes duplicate points, repairs self-intersections with the convex hull and
    rejects degenerate, tiny and implausibly large polygons."""
    pts = []
    for p in ring:
        if not pts or (abs(p[0] - pts[-1][0]) > 1e-8 or abs(p[1] - pts[-1][1]) > 1e-8):
            pts.append(p)
    while len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) < 1e-8 and abs(pts[0][1] - pts[-1][1]) < 1e-8:
        pts.pop()
    if len(pts) < 3:
        return None, "degenerate"
    xy = [proj.xy(*p) for p in pts]
    xs, ys = [q[0] for q in xy], [q[1] for q in xy]
    if max(xs) - min(xs) > MAX_SPAN_M or max(ys) - min(ys) > MAX_SPAN_M:
        return None, "huge"
    note = ""
    if self_intersects(xy):
        hull = convex_hull([(round(p[0], 7), round(p[1], 7)) for p in pts])
        if len(hull) < 3:
            return None, "degenerate"
        pts, note = hull, "repaired"
        xy = [proj.xy(*p) for p in pts]
    area = ring_area(xy)
    if abs(area) < MIN_AREA_M2:
        return None, "tiny"
    if abs(area) > MAX_AREA_M2:
        return None, "huge"
    if area < 0:  # clockwise in (east, north): flip so every footprint has the same winding
        pts.reverse()
        note = note or "reversed"
    return pts, note


def building_height(tags, area_m2, defaults, stats):
    """(height_m, min_height_m, source) with source 'osm', 'levels' or 'default'."""
    kind = tags.get("building", "yes")
    # "maxheight" on a building outline cannot be a vehicle clearance; mappers use it
    # for the structure's height (e.g. Burj Khalifa: maxheight=828, no height tag).
    h = (parse_height(tags.get("height")) or parse_height(tags.get("building:height"))
         or parse_height(tags.get("est_height")) or parse_height(tags.get("maxheight")))
    levels = parse_float(tags.get("building:levels"))
    src = "osm"
    if not h or h <= 0:
        if levels and levels > 0:
            h = levels * LEVEL_HEIGHT + parse_float(tags.get("roof:levels") or 0) * LEVEL_HEIGHT * 0.5
            src = "levels"
        else:
            if kind in defaults["kinds"]:
                h = float(defaults["kinds"][kind])
            elif kind in KIND_HEIGHT:
                h = KIND_HEIGHT[kind]
            elif area_m2 < defaults["small_area_m2"]:
                h = defaults["small_m"]
            elif area_m2 > defaults["large_area_m2"]:
                h = defaults["large_m"]
            else:
                h = defaults["generic_m"]
            src = "default"
    if h > MAX_HEIGHT:
        stats["height_clamped"] += 1
        h = MAX_HEIGHT
    h = max(h, MIN_HEIGHT)
    mh = parse_height(tags.get("min_height"))
    if mh is None and parse_float(tags.get("building:min_level")):
        mh = parse_float(tags.get("building:min_level")) * LEVEL_HEIGHT
    if mh is not None and (mh <= 0 or mh >= h - 1.0):
        mh = None  # inverted or zero-thickness: treat as a normal building
    stats["height_" + src] += 1
    return round(h, 1), (round(mh, 1) if mh else None), src


# ---------------------------------------------------------------------------
# Road network


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, a):
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def strongly_connected(adj):
    """Kosaraju, iterative. adj: {node: [node, ...]}. Returns the largest strongly
    connected component as a set: the nodes you can drive to and back from."""
    nodes = set(adj)
    for vs in adj.values():
        nodes.update(vs)
    radj = {}
    for u, vs in adj.items():
        for v in vs:
            radj.setdefault(v, []).append(u)
    seen, order = set(), []
    for start in nodes:
        if start in seen:
            continue
        seen.add(start)
        stack = [(start, iter(adj.get(start, ())))]
        while stack:
            u, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                stack.pop()
                order.append(u)
            elif nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, iter(adj.get(nxt, ()))))
    comp, best = set(), set()
    for start in reversed(order):
        if start in comp:
            continue
        cur = {start}
        comp.add(start)
        stack = [start]
        while stack:
            u = stack.pop()
            for v in radj.get(u, ()):
                if v not in comp:
                    comp.add(v)
                    cur.add(v)
                    stack.append(v)
        if len(cur) > len(best):
            best = cur
    return best


def directed_adjacency(roads):
    adj = {}
    for r in roads:
        if r.get("private"):
            continue
        for a, b in zip(r["nodes"], r["nodes"][1:]):
            adj.setdefault(a, []).append(b)
            if not r["oneway"]:
                adj.setdefault(b, []).append(a)
    return adj


def mark_connectivity(roads, proj, stats):
    """connected=true for roads with at least one segment in the main strongly connected
    network: you can drive onto them and away again, respecting one-way streets.
    Others (fragments cut off by the bbox, one-way stubs that only lead off the map,
    private compounds) are still drawn, but the game never spawns the car, resets it
    or puts a mission marker on them."""
    main = strongly_connected(directed_adjacency(roads))
    for r in roads:
        r["connected"] = (not r["private"]) and any(
            a in main and b in main for a, b in zip(r["nodes"], r["nodes"][1:]))
        if not r["connected"]:
            stats["roads_disconnected"] += 1
    stats["network_nodes"] = len(main)


def roundabouts_from(roads, proj):
    """Each ring of junction=roundabout ways (joined by shared nodes) becomes one roundabout."""
    uf = UnionFind()
    ring_roads = [r for r in roads if r["roundabout"]]
    for r in ring_roads:
        for a, b in zip(r["nodes"], r["nodes"][1:]):
            uf.union(a, b)
    groups = {}
    for r in ring_roads:
        g = groups.setdefault(uf.find(r["nodes"][0]), {"ids": set(), "names": set(), "pts": {}})
        g["ids"].add(r["id"])
        if r["name"]:
            g["names"].add(r["name"])
        for n, p in zip(r["nodes"], r["points"]):
            g["pts"][n] = p
    out = []
    for g in groups.values():
        pts = list(g["pts"].values())
        if len(pts) < 3:
            continue
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        c = proj.xy(lat, lon)
        radius = sum(math.hypot(proj.xy(*p)[0] - c[0], proj.xy(*p)[1] - c[1]) for p in pts) / len(pts)
        out.append({"ways": sorted(g["ids"]), "name": " / ".join(sorted(g["names"])),
                    "lat": r6(lat), "lon": r6(lon), "radius_m": round(radius, 1)})
    return sorted(out, key=lambda x: (x["lat"], x["lon"]))


def convert(district, raw):
    b = district["bbox"]
    clip = Clipper(b)
    lat0, lon0 = (b["south"] + b["north"]) / 2.0, (b["west"] + b["east"]) / 2.0
    proj = Projector(lat0, lon0)
    bdef = building_defaults(district)
    stats = {k: 0 for k in (
        "roads", "maxspeed_osm", "maxspeed_default", "roads_disconnected",
        "buildings", "height_osm", "height_levels", "height_default", "height_clamped",
        "bld_dropped_degenerate", "bld_dropped_tiny", "bld_dropped_huge",
        "bld_repaired", "bld_reversed", "bld_dropped_bridge")}
    default_by_class = {}
    roads, buildings, fuel, pois, signals = [], [], [], [], []
    seen_poi, seen_fuel = set(), set()

    for el in raw.get("elements", []):
        tags = el.get("tags", {})
        etype = el["type"]

        if etype == "way" and tags.get("highway") in DRIVABLE and "geometry" in el:
            hw = tags["highway"]
            if tags.get("area") == "yes":
                continue
            geom = el["geometry"]
            nodes = el.get("nodes") or []
            if len(nodes) != len(geom):
                # no node ids (old raw file): use the coordinates as node identity
                nodes = ["%.7f,%.7f" % (p["lat"], p["lon"]) if p else None for p in geom]
            pts = [(p["lat"], p["lon"]) if p else None for p in geom]
            if any(p is None for p in pts):
                continue
            ow = tags.get("oneway", "")
            roundabout = tags.get("junction") in ("roundabout", "circular")
            oneway = ow in ("yes", "1", "true", "-1") or (
                ow not in ("no", "false", "0") and (roundabout or hw in ("motorway", "motorway_link")))
            if ow == "-1":  # one-way against the drawing direction: store it in driving order
                pts, nodes = pts[::-1], nodes[::-1]
            speed = parse_maxspeed(tags.get("maxspeed"))
            speed_src = "osm"
            if speed is None:
                cls, speed = SPEED_CLASS.get(hw, ("residential", 40))
                speed_src = "default"
            bridge = is_yes(tags.get("bridge"))
            tunnel = is_yes(tags.get("tunnel"))
            layer = parse_float(tags.get("layer"))
            layer = int(layer) if layer is not None else (1 if bridge else (-1 if tunnel else 0))
            private = tags.get("access") in ("private", "no") or tags.get("service") in ("emergency_access",)
            for run in clip.split(pts):
                stats["roads"] += 1
                stats["maxspeed_" + speed_src] += 1
                if speed_src == "default":
                    key = "%s %d" % (SPEED_CLASS.get(hw, ("residential", 40))[0], speed)
                    default_by_class[key] = default_by_class.get(key, 0) + 1
                road = {
                    "id": element_id(el),
                    "name": tags.get("name:en") or tags.get("name") or "",
                    "ref": tags.get("ref", ""),
                    "highway": hw,
                    "maxspeed_kmh": speed,
                    "maxspeed_source": speed_src,
                    "lanes": int(parse_float(tags.get("lanes")) or 0) or None,
                    "oneway": oneway,
                    "points": [[r6(pts[i][0]), r6(pts[i][1])] for i in run],
                    "nodes": [nodes[i] for i in run],
                    "private": private,
                    "roundabout": roundabout,
                }
                if bridge:
                    road["bridge"] = True
                if tunnel:
                    road["tunnel"] = True
                if layer:
                    road["layer"] = layer
                roads.append(road)
            continue

        if etype == "node" and tags.get("highway") in ("traffic_signals", "stop", "give_way", "mini_roundabout"):
            if clip.inside(el["lat"], el["lon"]):
                signals.append({"id": element_id(el), "kind": tags["highway"],
                                "lat": r6(el["lat"]), "lon": r6(el["lon"])})
            continue

        if "building" in tags and etype in ("way", "relation") and (
                "geometry" in el or "members" in el):
            kind = tags.get("building", "yes")
            if kind == "bridge":
                stats["bld_dropped_bridge"] += 1  # drawn by the roads themselves
                continue
            rings = []
            if etype == "way":
                rings.append([(p["lat"], p["lon"]) for p in el["geometry"] if p])
            else:
                for m in el.get("members", []):
                    if m.get("role") == "outer" and m.get("geometry"):
                        rings.append([(p["lat"], p["lon"]) for p in m["geometry"] if p])
            for ring in rings:
                if len(ring) < 3 or not any(clip.inside(a, o) for a, o in ring):
                    continue
                cleaned, note = clean_ring(ring, proj)
                if cleaned is None:
                    stats["bld_dropped_" + note] += 1
                    continue
                if note:
                    stats["bld_" + note] += 1
                area = abs(ring_area([proj.xy(*p) for p in cleaned]))
                h, mh, hsrc = building_height(tags, area, bdef, stats)
                stats["buildings"] += 1
                bld = {
                    "id": element_id(el),
                    "name": tags.get("name:en") or tags.get("name") or "",
                    "kind": kind,
                    "height": h,
                    "height_source": hsrc,
                    "levels": parse_float(tags.get("building:levels")),
                    "points": [[r6(a), r6(o)] for a, o in cleaned],
                }
                if mh:
                    bld["min_height"] = mh
                if kind in NON_SOLID_KINDS or mh:
                    bld["solid"] = False  # canopies and raised structures: the car passes under
                buildings.append(bld)

        if tags.get("amenity") == "fuel":
            c = element_center(el)
            if c and clip.inside(*c) and element_id(el) not in seen_fuel:
                seen_fuel.add(element_id(el))
                fuel.append({
                    "id": element_id(el),
                    "name": tags.get("name:en") or tags.get("name") or "",
                    "brand": tags.get("brand:en") or tags.get("brand") or tags.get("operator") or "",
                    "lat": r6(c[0]), "lon": r6(c[1]),
                })

        if "name" in tags:
            cat = poi_category(tags)
            c = element_center(el)
            if cat and c and clip.inside(*c) and element_id(el) not in seen_poi:
                seen_poi.add(element_id(el))
                pois.append({
                    "id": element_id(el),
                    "name": tags.get("name"),
                    "name_en": tags.get("name:en") or "",
                    "category": cat,
                    "lat": r6(c[0]), "lon": r6(c[1]),
                })

    # The same station mapped as a node inside its forecourt way: keep the way.
    fuel = dedupe_fuel(fuel, proj)
    mark_connectivity(roads, proj, stats)
    fuel_access(fuel, roads, proj, stats)
    roundabouts = roundabouts_from(roads, proj)
    stats["roundabouts"] = len(roundabouts)
    stats["signals"] = sum(1 for s in signals if s["kind"] == "traffic_signals")
    stats["fuel"] = len(fuel)
    stats["pois"] = len(pois)

    return {
        "district": district["id"],
        "source": "OpenStreetMap contributors, ODbL (via Overpass API)",
        "osm_base": raw.get("osm3s", {}).get("timestamp_osm_base", ""),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bbox": b,
        "origin": {"lat": r6(lat0), "lon": r6(lon0)},
        "stats": stats,
        "maxspeed_defaults_used": dict(sorted(default_by_class.items())),
        "building_defaults": bdef,
        "roads": roads,
        "buildings": buildings,
        "fuel": fuel,
        "roundabouts": roundabouts,
        "signals": signals,
        "pois": sorted(pois, key=lambda p: (p["category"], p["name"])),
    }


BARRIER_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link"}


def barrier_segments(roads, proj):
    """Ground-level motorway/trunk carriageways: a place on one side is not served by a
    road on the other side."""
    segs = []
    for r in roads:
        if r["highway"] in BARRIER_HIGHWAYS and not r.get("bridge") and not r.get("tunnel"):
            pts = [proj.xy(*p) for p in r["points"]]
            segs += list(zip(pts, pts[1:]))
    return segs


def crosses(a, b, segs):
    for p, q in segs:
        if (max(p[0], q[0]) < min(a[0], b[0]) or min(p[0], q[0]) > max(a[0], b[0]) or
                max(p[1], q[1]) < min(a[1], b[1]) or min(p[1], q[1]) > max(a[1], b[1])):
            continue
        if _seg_cross(a, b, p, q):
            return True
    return False


def nearest_on_roads(lat, lon, roads, proj, ok, max_m, barriers=()):
    px, py = proj.xy(lat, lon)
    cands = []
    for r in roads:
        if not ok(r):
            continue
        pts = [proj.xy(*p) for p in r["points"]]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy
            t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
            qx, qy = ax + t * dx, ay + t * dy
            d = math.hypot(px - qx, py - qy)
            if d <= max_m:
                cands.append((d, qx, qy, r))
    cands.sort(key=lambda c: c[0])
    for d, qx, qy, r in cands:
        if not crosses((px, py), (qx, qy), barriers):
            return d, qx, qy, r
    return None


def fuel_access(fuel, roads, proj, stats):
    """Where the car stops to refuel: the nearest point on a public road, preferring the
    forecourt service lanes (highway=service) right next to the station, never across a
    motorway from it. Roads in the main network come first; a station whose
    neighbourhood is cut off by the map edge still gets its own forecourt."""
    barriers = barrier_segments(roads, proj)

    def public(r):
        return not r.get("private") and not r.get("tunnel") and r["highway"] != "motorway"
    tries = [
        (lambda r: public(r) and r["connected"] and r["highway"] == "service", 60.0),
        (lambda r: public(r) and r["connected"], 200.0),
        (lambda r: public(r) and r["highway"] == "service", 60.0),
        (public, 200.0),
    ]
    for f in fuel:
        hit = None
        for ok, dist in tries:
            hit = nearest_on_roads(f["lat"], f["lon"], roads, proj, ok, dist, barriers)
            if hit:
                break
        if hit is None:
            stats["fuel_unreachable"] = stats.get("fuel_unreachable", 0) + 1
            continue
        d, x, y, r = hit
        f["access"] = {"lat": r6(proj.lat0 + y / proj.ky), "lon": r6(proj.lon0 + x / proj.kx),
                       "road": r["name"] or r["highway"], "snap_m": round(d, 1),
                       "on_main_network": r["connected"]}


def dedupe_fuel(fuel, proj, radius_m=60.0):
    fuel = sorted(fuel, key=lambda f: (not f["id"].startswith("way/"), f["id"]))
    kept = []
    for f in fuel:
        x, y = proj.xy(f["lat"], f["lon"])
        dup = False
        for k in kept:
            kx, ky = proj.xy(k["lat"], k["lon"])
            if math.hypot(x - kx, y - ky) < radius_m and (
                    not f["brand"] or not k["brand"] or f["brand"] == k["brand"]):
                dup = True
                k["name"] = k["name"] or f["name"]
                k["brand"] = k["brand"] or f["brand"]
                break
        if not dup:
            kept.append(f)
    return kept


def print_stats(out):
    s = out["stats"]
    print("  roads=%d (maxspeed from OSM %d, default %d; %d outside the main drivable network "
          "of %d junction nodes)" % (s["roads"], s["maxspeed_osm"], s["maxspeed_default"],
                                     s["roads_disconnected"], s["network_nodes"]))
    if out["maxspeed_defaults_used"]:
        print("    default speed limits used: %s" % ", ".join(
            "%s km/h x%d" % (k, v) for k, v in out["maxspeed_defaults_used"].items()))
    bd = out["building_defaults"]
    print("  buildings=%d (height from OSM %d, from levels x %.1f m %d, guessed %d; clamped %d)" % (
        s["buildings"], s["height_osm"], LEVEL_HEIGHT, s["height_levels"], s["height_default"],
        s["height_clamped"]))
    print("    guess rule: footprint <%d m2 -> %.0f m, %d-%d m2 -> %.0f m, larger -> %.0f m, "
          "known kinds (house, garage, mosque...) by kind" % (
              bd["small_area_m2"], bd["small_m"], bd["small_area_m2"], bd["large_area_m2"],
              bd["generic_m"], bd["large_m"]))
    print("    polygon fixes: repaired %d self-intersecting, reversed %d; dropped %d degenerate, "
          "%d tiny, %d huge, %d building=bridge" % (
              s["bld_repaired"], s["bld_reversed"], s["bld_dropped_degenerate"],
              s["bld_dropped_tiny"], s["bld_dropped_huge"], s["bld_dropped_bridge"]))
    print("  fuel=%d roundabouts=%d traffic_signals=%d pois=%d" % (
        s["fuel"], s["roundabouts"], s["signals"], s["pois"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("districts", nargs="*", help="district ids (default: all)")
    ap.add_argument("--endpoint", action="append", help="Overpass endpoint URL (repeatable)")
    ap.add_argument("--raw-only", action="store_true", help="only download raw Overpass JSON")
    ap.add_argument("--from-raw", action="store_true", help="convert data/osm_raw/<id>.json, no download")
    ap.add_argument("--print-query", action="store_true", help="print the Overpass query and exit")
    ap.add_argument("--osm-api", action="store_true",
                    help="skip Overpass and read the official OSM API directly (tiled)")
    ap.add_argument("--rounds", type=int, default=3, help="passes over the Overpass mirrors")
    args = ap.parse_args()

    districts = load_districts(set(args.districts))
    if not districts:
        sys.exit("no districts found in %s" % DISTRICT_DIR)
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)

    failed = []
    for d in districts:
        print("[%s] %s" % (d["id"], d["name"]))
        query = build_query(d["bbox"])
        if args.print_query:
            print(query)
            continue
        raw_path = os.path.join(RAW_DIR, d["id"] + ".json")
        try:
            if args.from_raw:
                with open(raw_path, encoding="utf-8") as f:
                    raw = json.load(f)
            else:
                raw = None
                if not args.osm_api:
                    try:
                        raw = download(query, args.endpoint or ENDPOINTS, rounds=args.rounds)
                    except RuntimeError as e:
                        print("  Overpass unavailable (%s); using the OSM API instead" % e)
                if raw is None:
                    raw = download_osm_api(d["bbox"])
                with open(raw_path, "w", encoding="utf-8") as f:
                    json.dump(raw, f)
                time.sleep(2)  # be polite to the public Overpass servers
        except (RuntimeError, OSError, ValueError) as e:
            print("  ERROR: %s" % e)
            failed.append(d["id"])
            continue
        if args.raw_only:
            continue
        out = convert(d, raw)
        out_path = os.path.join(OUT_DIR, d["id"] + ".json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        print_stats(out)
        print("  -> %s" % os.path.relpath(out_path, ROOT))

    if failed:
        sys.exit("failed districts: %s" % ", ".join(failed))


if __name__ == "__main__":
    main()
