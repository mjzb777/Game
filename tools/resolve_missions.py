#!/usr/bin/env python3
"""Match every mission place in data/districts/*.json against the downloaded OSM data.

Each place in a mission ("target", "pickup", "dropoff") lists the names it wants:

    "target": {"names": ["The Dubai Mall", "Dubai Mall"],
               "categories": ["shop=mall"],          # optional, used to pick between matches
               "allow_fallback": true}               # optional, see below

For each place this script looks the names up in data/osm/<id>.json (POIs, fuel
stations and named buildings) and writes the real OSM element into the place:

    "resolved": {"osm_id": "way/123", "name": "The Dubai Mall", "lat": .., "lon": .., "method": "name"}

If no name matches and "allow_fallback" is true, the nearest named POI of one of the
listed categories is used instead (method "category") and the mission text, which uses
{target}/{pickup}/{dropoff} placeholders, shows that real name. If nothing matches,
"resolved" is removed and the game disables the mission. Coordinates only ever come
from the OSM data.

A report is written to data/place_verification.md.

Usage:
    python3 tools/resolve_missions.py
    python3 tools/resolve_missions.py --list-pois downtown [--grep mall]
"""

import argparse
import json
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISTRICT_DIR = os.path.join(ROOT, "data", "districts")
OSM_DIR = os.path.join(ROOT, "data", "osm")
REPORT = os.path.join(ROOT, "data", "place_verification.md")
ROLES = ("pickup", "dropoff", "target")


def norm(s):
    s = (s or "").lower().replace("’", "'").replace("'", "")
    s = re.sub(r"[^a-z0-9؀-ۿ]+", " ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    return s


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
        if f.get("name") or f.get("brand"):
            places.append({"id": f["id"], "name": f.get("name") or f.get("brand"),
                           "name_en": f.get("brand", ""), "category": "amenity=fuel",
                           "lat": f["lat"], "lon": f["lon"]})
    for b in osm.get("buildings", []):
        if b.get("name"):
            pts = b["points"]
            places.append({"id": b["id"], "name": b["name"], "name_en": "",
                           "category": "building",
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


def find_by_category(places, ref, origin, used):
    cats = set(ref.get("categories", []))
    cands = [p for p in places if p["category"] in cats]
    if not cands:
        return None
    return sorted(cands, key=lambda p: (p["id"] in used,
                                        dist_m(origin["lat"], origin["lon"], p["lat"], p["lon"])))[0]


def resolve_district(district, report):
    path = osm_path(district)
    lines = ["## %s (`%s`)" % (district["name"], district["id"]), ""]
    unverified = []
    if not os.path.exists(path):
        lines += ["**No OSM data** (`%s` missing). Run `python3 tools/fetch_osm.py %s` first. "
                  "All %d missions are disabled until then." % (
                      os.path.relpath(path, ROOT), district["id"], len(district["missions"])), ""]
        for m in district["missions"]:
            for role in ROLES:
                if role in m:
                    m[role].pop("resolved", None)
                    unverified.append((m["id"], role, m[role].get("names", [])))
        report.append("\n".join(lines))
        return unverified, 0

    with open(path, encoding="utf-8") as f:
        osm = json.load(f)
    places = load_places(osm)
    origin = osm["origin"]
    used = set()
    ok_missions = 0
    lines += ["| Mission | Role | Wanted | OSM match | lat, lon | How |",
              "|---|---|---|---|---|---|"]
    for m in district["missions"]:
        all_ok = True
        for role in ROLES:
            ref = m.get(role)
            if ref is None:
                continue
            hit, method = find_by_name(places, ref), "name"
            if hit is None and ref.get("allow_fallback"):
                hit, method = find_by_category(places, ref, origin, used), "category"
            wanted = " / ".join(ref.get("names", [])) or "(any %s)" % ", ".join(ref.get("categories", []))
            if hit is None:
                ref.pop("resolved", None)
                all_ok = False
                unverified.append((m["id"], role, ref.get("names", [])))
                lines.append("| %s | %s | %s | **NOT FOUND** | | |" % (m["id"], role, wanted))
                continue
            used.add(hit["id"])
            ref["resolved"] = {"osm_id": hit["id"], "name": hit.get("name_en") or hit["name"],
                               "category": hit["category"], "lat": round(hit["lat"], 6),
                               "lon": round(hit["lon"], 6), "method": method}
            if method == "category":
                unverified.append((m["id"], role, ref.get("names", [])))
            lines.append("| %s | %s | %s | %s `%s` | %.6f, %.6f | %s |" % (
                m["id"], role, wanted, ref["resolved"]["name"], hit["id"], hit["lat"], hit["lon"],
                "name match" if method == "name" else "**category fallback**"))
        ok_missions += 1 if all_ok else 0
    lines.append("")
    report.append("\n".join(lines))
    return unverified, ok_missions


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
            line = "%-28s %-40s %-14s %.6f, %.6f" % (p["category"], p["name"], p["id"], p["lat"], p["lon"])
            if not args.grep or args.grep.lower() in line.lower():
                print(line)
        return

    report = ["# Mission place verification", "",
              "Generated by `tools/resolve_missions.py` from the OSM files in `data/osm/`.", ""]
    all_unverified = []
    total_ok = 0
    for fn, d in districts:
        unverified, ok = resolve_district(d, report)
        total_ok += ok
        all_unverified += [(d["id"],) + u for u in unverified]
        with open(os.path.join(DISTRICT_DIR, fn), "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print("%-12s %d/%d missions playable" % (d["id"], ok, len(d["missions"])))

    report += ["## Places that could not be verified by name", ""]
    if all_unverified:
        report += ["These names were not found in the downloaded data (or the district has no "
                   "data yet). Places with `allow_fallback` use the nearest real OSM place of the "
                   "same category instead; missions with any other unmatched place are disabled.", "",
                   "| District | Mission | Role | Wanted names |", "|---|---|---|---|"]
        report += ["| %s | %s | %s | %s |" % (d, m, r, " / ".join(n) or "-") for d, m, r, n in all_unverified]
    else:
        report.append("None - every place was found by name.")
    report.append("")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("%d missions playable; %d place(s) not verified by name. Report: %s" % (
        total_ok, len(all_unverified), os.path.relpath(REPORT, ROOT)))


if __name__ == "__main__":
    main()
