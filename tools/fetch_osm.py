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
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
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
        "(",
        *poi_lines,
        ")->.pois;",
        ".roads out tags geom;",
        ".buildings out tags geom;",
        ".fuel out tags center;",
        ".pois out tags center;",
    ])


def download(query, endpoints):
    data = urllib.parse.urlencode({"data": query}).encode()
    last_err = None
    for url in endpoints:
        for attempt in range(3):
            try:
                print("  POST %s (attempt %d)" % (url, attempt + 1))
                req = urllib.request.Request(url, data=data, headers={
                    "User-Agent": "dubai-driving-game-osm-fetch/1.0",
                    "Content-Type": "application/x-www-form-urlencoded",
                })
                with urllib.request.urlopen(req, timeout=300) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, OSError, ValueError) as e:
                last_err = e
                print("    failed: %s" % e)
                if "403" in str(e):
                    break  # blocked by a proxy or the server: try the next endpoint
                time.sleep(5 * (attempt + 1))
    raise RuntimeError("all Overpass endpoints failed: %s" % last_err)


def parse_maxspeed(v):
    if not v:
        return None
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph)?\s*$", v)
    if not m:
        return None
    speed = float(m.group(1))
    if m.group(2) == "mph":
        speed *= 1.609344
    return int(round(speed))


def parse_float(v):
    if v is None:
        return None
    m = re.match(r"^\s*(\d+(?:\.\d+)?)", str(v))
    return float(m.group(1)) if m else None


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
        """Split a polyline into runs that stay inside the clip box.
        One point outside the box is kept at each end so roads reach the edge."""
        runs, cur = [], []
        for i, (lat, lon) in enumerate(pts):
            if self.inside(lat, lon):
                if not cur and i > 0:
                    cur.append(pts[i - 1])
                cur.append((lat, lon))
            elif cur:
                cur.append((lat, lon))
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        return [r for r in runs if len(r) >= 2]


def r6(v):
    return round(v, 6)


def convert(district, raw):
    b = district["bbox"]
    clip = Clipper(b)
    roads, buildings, fuel, pois = [], [], [], []
    seen_poi = set()

    for el in raw.get("elements", []):
        tags = el.get("tags", {})
        etype = el["type"]

        if etype == "way" and tags.get("highway") in DRIVABLE and "geometry" in el:
            pts = [(p["lat"], p["lon"]) for p in el["geometry"] if p]
            for run in clip.split(pts):
                roads.append({
                    "id": element_id(el),
                    "name": tags.get("name:en") or tags.get("name") or "",
                    "highway": tags["highway"],
                    "maxspeed_kmh": parse_maxspeed(tags.get("maxspeed")),
                    "lanes": int(parse_float(tags.get("lanes")) or 0) or None,
                    "oneway": tags.get("oneway") in ("yes", "1", "true"),
                    "points": [[r6(a), r6(o)] for a, o in run],
                })
            continue

        if "building" in tags and etype in ("way", "relation") and (
                "geometry" in el or "members" in el):
            rings = []
            if etype == "way":
                rings.append([(p["lat"], p["lon"]) for p in el["geometry"] if p])
            else:
                for m in el.get("members", []):
                    if m.get("role") == "outer" and m.get("geometry"):
                        rings.append([(p["lat"], p["lon"]) for p in m["geometry"] if p])
            for ring in rings:
                if len(ring) < 4 or not any(clip.inside(a, o) for a, o in ring):
                    continue
                if ring[0] == ring[-1]:
                    ring = ring[:-1]
                buildings.append({
                    "id": element_id(el),
                    "name": tags.get("name:en") or tags.get("name") or "",
                    "kind": tags.get("building", "yes"),
                    "min_height": parse_float(tags.get("min_height")),
                    "height": parse_float(tags.get("height")),
                    "levels": parse_float(tags.get("building:levels")),
                    "points": [[r6(a), r6(o)] for a, o in ring],
                })

        if tags.get("amenity") == "fuel":
            c = element_center(el)
            if c and clip.inside(*c):
                fuel.append({
                    "id": element_id(el),
                    "name": tags.get("name:en") or tags.get("name") or "",
                    "brand": tags.get("brand") or tags.get("operator") or "",
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

    # A building with a name usually also appears as a POI; buildings are kept regardless.
    # Merge duplicate fuel entries (node + way for the same station).
    uniq_fuel, fuel_ids = [], set()
    for f in fuel:
        if f["id"] not in fuel_ids:
            fuel_ids.add(f["id"])
            uniq_fuel.append(f)

    return {
        "district": district["id"],
        "source": "OpenStreetMap contributors, ODbL (via Overpass API)",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bbox": b,
        "origin": {"lat": r6((b["south"] + b["north"]) / 2.0),
                   "lon": r6((b["west"] + b["east"]) / 2.0)},
        "roads": roads,
        "buildings": buildings,
        "fuel": uniq_fuel,
        "pois": sorted(pois, key=lambda p: (p["category"], p["name"])),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("districts", nargs="*", help="district ids (default: all)")
    ap.add_argument("--endpoint", action="append", help="Overpass endpoint URL (repeatable)")
    ap.add_argument("--raw-only", action="store_true", help="only download raw Overpass JSON")
    ap.add_argument("--from-raw", action="store_true", help="convert data/osm_raw/<id>.json, no download")
    ap.add_argument("--print-query", action="store_true", help="print the Overpass query and exit")
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
                raw = download(query, args.endpoint or ENDPOINTS)
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
        print("  roads=%d buildings=%d fuel=%d pois=%d -> %s" % (
            len(out["roads"]), len(out["buildings"]), len(out["fuel"]), len(out["pois"]),
            os.path.relpath(out_path, ROOT)))

    if failed:
        sys.exit("failed districts: %s" % ", ".join(failed))


if __name__ == "__main__":
    main()
