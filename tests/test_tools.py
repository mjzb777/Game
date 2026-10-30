#!/usr/bin/env python3
"""Unit tests for tools/fetch_osm.py (conversion) and tools/resolve_missions.py (matching).
Uses a small fake Overpass response; no network needed.  Run: python3 tests/test_tools.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import fetch_osm  # noqa: E402
import resolve_missions  # noqa: E402

DISTRICT = {"id": "t", "name": "T", "bbox": {"south": 25.19, "west": 55.27, "north": 25.21, "east": 55.29}}
RAW = {"elements": [
    {"type": "way", "id": 1, "tags": {"highway": "primary", "name": "Test Rd", "maxspeed": "60", "lanes": "3"},
     "geometry": [{"lat": 25.20, "lon": 55.20}, {"lat": 25.20, "lon": 55.28}, {"lat": 25.201, "lon": 55.285}]},
    {"type": "way", "id": 2, "tags": {"highway": "footway"}, "geometry": [{"lat": 25.2, "lon": 55.28}, {"lat": 25.201, "lon": 55.28}]},
    {"type": "way", "id": 3, "tags": {"building": "retail", "name": "The Dubai Mall", "building:levels": "4"},
     "geometry": [{"lat": 25.198, "lon": 55.279}, {"lat": 25.198, "lon": 55.280}, {"lat": 25.199, "lon": 55.280},
                  {"lat": 25.199, "lon": 55.279}, {"lat": 25.198, "lon": 55.279}]},
    {"type": "node", "id": 4, "lat": 25.205, "lon": 55.275, "tags": {"amenity": "fuel", "brand": "ENOC"}},
    {"type": "node", "id": 5, "lat": 25.201, "lon": 55.27, "tags": {"railway": "station", "name": "Burj Khalifa/Dubai Mall"}},
    {"type": "way", "id": 6, "center": {"lat": 25.196, "lon": 55.281}, "tags": {"shop": "mall", "name": "Dubai Mall Annex"}},
    {"type": "node", "id": 7, "lat": 25.25, "lon": 55.4, "tags": {"amenity": "cafe", "name": "Far Away Cafe"}},
    {"type": "node", "id": 8, "lat": 25.202, "lon": 55.282, "tags": {"amenity": "place_of_worship", "name": "Test Mosque"}},
]}


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.out = fetch_osm.convert(DISTRICT, RAW)

    def test_roads(self):
        self.assertEqual(len(self.out["roads"]), 1, "footway is not drivable")
        r = self.out["roads"][0]
        self.assertEqual((r["name"], r["maxspeed_kmh"], r["lanes"]), ("Test Rd", 60, 3))
        self.assertEqual(len(r["points"]), 3, "clipped to bbox, keeping one point outside")

    def test_buildings_fuel_pois(self):
        b = self.out["buildings"][0]
        self.assertEqual((b["name"], b["levels"], len(b["points"])), ("The Dubai Mall", 4.0, 4))
        self.assertEqual(self.out["fuel"][0]["brand"], "ENOC")
        names = {p["name"] for p in self.out["pois"]}
        self.assertIn("Burj Khalifa/Dubai Mall", names)
        self.assertIn("The Dubai Mall", names)  # building=retail with a name
        self.assertNotIn("Far Away Cafe", names, "outside bbox")

    def test_maxspeed(self):
        self.assertEqual(fetch_osm.parse_maxspeed("50 mph"), 80)
        self.assertIsNone(fetch_osm.parse_maxspeed("signals"))


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.places = resolve_missions.load_places(fetch_osm.convert(DISTRICT, RAW))

    def test_exact_beats_partial(self):
        hit = resolve_missions.find_by_name(self.places, {"names": ["Dubai Mall"], "categories": ["shop=mall"]})
        self.assertEqual(hit["name"], "The Dubai Mall", "'the' is ignored and exact match wins")

    def test_station_variants(self):
        hit = resolve_missions.find_by_name(self.places, {"names": ["Burj Khalifa / Dubai Mall"]})
        self.assertEqual(hit["id"], "node/5")

    def test_not_found_and_fallback(self):
        ref = {"names": ["Downtown Mosque"], "categories": ["amenity=place_of_worship"]}
        self.assertIsNone(resolve_missions.find_by_name(self.places, ref))
        hit = resolve_missions.find_by_category(self.places, ref, {"lat": 25.2, "lon": 55.28}, set())
        self.assertEqual(hit["name"], "Test Mosque")

    def test_fuel_brand(self):
        hit = resolve_missions.find_by_name(self.places, {"names": ["ENOC"], "categories": ["amenity=fuel"]})
        self.assertEqual(hit["id"], "node/4")


if __name__ == "__main__":
    unittest.main()
