#!/usr/bin/env python3
"""Unit tests for tools/fetch_osm.py (conversion) and tools/resolve_missions.py (matching,
road snapping, routing). Uses a small fake Overpass response; no network needed.
Run: python3 tests/test_tools.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import fetch_osm  # noqa: E402
import resolve_missions  # noqa: E402

DISTRICT = {"id": "t", "name": "T", "bbox": {"south": 25.19, "west": 55.27, "north": 25.21, "east": 55.29},
            "building_defaults": {"generic_m": 40.0, "small_m": 7.0}}


def g(*pts):
    return [{"lat": a, "lon": o} for a, o in pts]


RAW = {"elements": [
    # Test Rd runs west-east along lat 25.20 and starts outside the bbox.
    {"type": "way", "id": 1, "nodes": [10, 11, 12],
     "tags": {"highway": "primary", "name": "Test Rd", "maxspeed": "60", "lanes": "3"},
     "geometry": g((25.20, 55.20), (25.20, 55.28), (25.201, 55.285))},
    {"type": "way", "id": 2, "nodes": [11, 13], "tags": {"highway": "footway"},
     "geometry": g((25.2, 55.28), (25.201, 55.28))},
    # Side St: no maxspeed (default 40), joins Test Rd at node 11 and goes north.
    {"type": "way", "id": 20, "nodes": [11, 21, 22], "tags": {"highway": "residential", "name": "Side St"},
     "geometry": g((25.20, 55.28), (25.203, 55.28), (25.206, 55.28))},
    # One-way drawn south->north but tagged -1: legal direction is north->south.
    {"type": "way", "id": 30, "nodes": [22, 31], "tags": {"highway": "tertiary", "oneway": "-1"},
     "geometry": g((25.206, 55.28), (25.206, 55.284))},
    # Loop Rd closes the block, so the one-way is part of the drivable network.
    {"type": "way", "id": 70, "nodes": [12, 31], "tags": {"highway": "residential", "name": "Loop Rd"},
     "geometry": g((25.201, 55.285), (25.206, 55.284))},
    # A one-way stub that only leads off the map: drawn, but not routable.
    {"type": "way", "id": 80, "nodes": [12, 81], "tags": {"highway": "residential", "oneway": "yes", "name": "Exit Only"},
     "geometry": g((25.201, 55.285), (25.199, 55.30))},
    # A bridge over Test Rd with no shared nodes, and an isolated service road.
    {"type": "way", "id": 40, "nodes": [41, 42], "tags": {"highway": "motorway", "bridge": "yes", "layer": "1"},
     "geometry": g((25.195, 55.282), (25.205, 55.282))},
    {"type": "way", "id": 50, "nodes": [51, 52], "tags": {"highway": "service", "access": "private"},
     "geometry": g((25.208, 55.271), (25.208, 55.273))},
    {"type": "way", "id": 3, "tags": {"building": "retail", "name": "The Dubai Mall", "building:levels": "4"},
     "geometry": g((25.198, 55.279), (25.198, 55.280), (25.199, 55.280), (25.199, 55.279), (25.198, 55.279))},
    # Self-intersecting "bow tie" footprint, drawn clockwise.
    {"type": "way", "id": 60, "tags": {"building": "yes"},
     "geometry": g((25.193, 55.275), (25.194, 55.276), (25.193, 55.276), (25.194, 55.275), (25.193, 55.275))},
    # Huge bogus building outline (3 km across).
    {"type": "way", "id": 61, "tags": {"building": "yes"},
     "geometry": g((25.19, 55.27), (25.19, 55.30), (25.21, 55.30), (25.21, 55.27), (25.19, 55.27))},
    # Small untagged footprint (villa-sized, ~11 x 11 m) and a tagged height.
    {"type": "way", "id": 62, "tags": {"building": "yes"},
     "geometry": g((25.205, 55.275), (25.2051, 55.275), (25.2051, 55.2751), (25.205, 55.2751))},
    {"type": "way", "id": 63, "tags": {"building": "yes", "height": "120 m"},
     "geometry": g((25.196, 55.285), (25.1965, 55.285), (25.1965, 55.2855), (25.196, 55.2855))},
    {"type": "node", "id": 4, "lat": 25.2001, "lon": 55.2805, "tags": {"amenity": "fuel", "brand": "ENOC"}},
    {"type": "node", "id": 5, "lat": 25.201, "lon": 55.27, "tags": {"railway": "station", "name": "Burj Khalifa/Dubai Mall"}},
    {"type": "way", "id": 6, "center": {"lat": 25.196, "lon": 55.281}, "tags": {"shop": "mall", "name": "Dubai Mall Annex"}},
    {"type": "node", "id": 7, "lat": 25.25, "lon": 55.4, "tags": {"amenity": "cafe", "name": "Far Away Cafe"}},
    {"type": "node", "id": 8, "lat": 25.2045, "lon": 55.2803, "tags": {"amenity": "place_of_worship", "name": "Test Mosque"}},
    {"type": "node", "id": 21, "lat": 25.203, "lon": 55.28, "tags": {"highway": "traffic_signals"}},
]}


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.out = fetch_osm.convert(DISTRICT, RAW)
        self.roads = {r["id"]: r for r in self.out["roads"]}

    def test_roads(self):
        self.assertNotIn("way/2", self.roads, "footway is not drivable")
        r = self.roads["way/1"]
        self.assertEqual((r["name"], r["maxspeed_kmh"], r["maxspeed_source"], r["lanes"]), ("Test Rd", 60, "osm", 3))
        self.assertEqual(len(r["points"]), 3, "clipped to bbox, keeping one point outside")
        self.assertEqual(len(r["nodes"]), len(r["points"]))

    def test_default_speed_and_log(self):
        self.assertEqual((self.roads["way/20"]["maxspeed_kmh"], self.roads["way/20"]["maxspeed_source"]), (40, "default"))
        self.assertEqual(self.roads["way/30"]["maxspeed_kmh"], 60, "tertiary = arterial 60")
        self.assertEqual(self.roads["way/40"]["maxspeed_kmh"], 120, "motorway 120")
        self.assertEqual(self.out["stats"]["maxspeed_default"], 6)

    def test_oneway_reversed(self):
        r = self.roads["way/30"]
        self.assertTrue(r["oneway"])
        self.assertEqual(r["nodes"], [31, 22], "oneway=-1 stored in driving order")
        self.assertTrue(self.roads["way/40"]["oneway"], "motorways are implied one-way")

    def test_bridge_and_connectivity(self):
        self.assertTrue(self.roads["way/40"].get("bridge"))
        self.assertEqual(self.roads["way/40"].get("layer"), 1)
        self.assertFalse(self.roads["way/40"]["connected"], "bridge shares no node with the network")
        self.assertFalse(self.roads["way/50"]["connected"], "private road")
        self.assertFalse(self.roads["way/80"]["connected"], "one-way that never comes back")
        self.assertTrue(self.roads["way/20"]["connected"])
        self.assertTrue(self.roads["way/30"]["connected"], "one-way inside the loop")

    def test_buildings(self):
        b = {x["id"]: x for x in self.out["buildings"]}
        mall = b["way/3"]
        self.assertEqual((mall["name"], mall["levels"], len(mall["points"])), ("The Dubai Mall", 4.0, 4))
        self.assertAlmostEqual(mall["height"], 12.8)
        self.assertEqual(mall["height_source"], "levels")
        self.assertNotIn("way/61", b, "huge outline dropped")
        self.assertEqual(self.out["stats"]["bld_dropped_huge"], 1)
        self.assertEqual(self.out["stats"]["bld_repaired"], 1, "bow tie repaired")
        self.assertEqual((b["way/62"]["height"], b["way/62"]["height_source"]), (7.0, "default"))
        self.assertEqual((b["way/63"]["height"], b["way/63"]["height_source"]), (120.0, "osm"))
        # every footprint is counter-clockwise seen from above (east, north)
        proj = fetch_osm.Projector(25.2, 55.28)
        for x in b.values():
            self.assertGreater(fetch_osm.ring_area([proj.xy(*p) for p in x["points"]]), 0, x["id"])

    def test_fuel_pois_signals(self):
        f = self.out["fuel"][0]
        self.assertEqual(f["brand"], "ENOC")
        self.assertIn("access", f, "station gets a road access point")
        self.assertLess(f["access"]["snap_m"], 30)
        names = {p["name"] for p in self.out["pois"]}
        self.assertIn("Burj Khalifa/Dubai Mall", names)
        self.assertIn("The Dubai Mall", names)
        self.assertNotIn("Far Away Cafe", names, "outside bbox")
        self.assertEqual(self.out["stats"]["signals"], 1)

    def test_parsers(self):
        self.assertEqual(fetch_osm.parse_maxspeed("50 mph"), 80)
        self.assertEqual(fetch_osm.parse_maxspeed("60;80"), 60)
        self.assertIsNone(fetch_osm.parse_maxspeed("signals"))
        self.assertIsNone(fetch_osm.parse_maxspeed("AE:urban"))
        self.assertAlmostEqual(fetch_osm.parse_height("100 ft"), 30.48)


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.osm = fetch_osm.convert(DISTRICT, RAW)
        self.places = resolve_missions.load_places(self.osm)
        self.graph = resolve_missions.RoadGraph(self.osm)

    def test_exact_beats_partial(self):
        hit = resolve_missions.find_by_name(self.places, {"names": ["Dubai Mall"], "categories": ["shop=mall"]})
        self.assertEqual(hit["name"], "The Dubai Mall", "'the' is ignored and exact match wins")

    def test_station_variants(self):
        hit = resolve_missions.find_by_name(self.places, {"names": ["Burj Khalifa / Dubai Mall"]})
        self.assertEqual(hit["id"], "node/5")

    def test_not_found_is_an_error(self):
        ref = {"names": ["Downtown Mosque"], "categories": ["amenity=place_of_worship"]}
        snap, err = resolve_missions.resolve_place(ref, self.places, self.graph)
        self.assertIsNone(snap)
        self.assertIn("not found", err)
        self.assertNotIn("resolved", ref)

    def test_place_snaps_to_drivable_road(self):
        ref = {"names": ["Test Mosque"]}
        snap, err = resolve_missions.resolve_place(ref, self.places, self.graph)
        self.assertIsNone(err)
        acc = ref["resolved"]["access"]
        self.assertEqual(acc["road"], "Side St", "not the motorway bridge, which is closer")
        self.assertLess(acc["snap_m"], 40)

    def test_route_respects_oneway(self):
        a = self.graph.snap(25.2, 55.2795, resolve_missions.stoppable)   # Test Rd west of the junction
        b = self.graph.snap(25.2061, 55.2835, resolve_missions.stoppable)  # the one-way, near its east end
        self.assertEqual(self.graph.roads[b["road_index"]]["id"], "way/30")
        fwd = self.graph.route(a, b)
        self.assertIn("Loop Rd", fwd["roads"], "the one-way only runs east->west: go round the block")
        back = self.graph.route(b, a)
        self.assertIsNotNone(back)
        self.assertGreater(back["distance_m"], 900)
        self.assertEqual(back["signals"], 1)
        self.assertEqual(back["roads"][:2], ["Side St", "Test Rd"])
        self.assertNotIn("Loop Rd", back["roads"])

    def test_no_snap_to_dead_end(self):
        s = self.graph.snap(25.1995, 55.297, resolve_missions.stoppable, max_m=2000)
        self.assertNotEqual(self.graph.roads[s["road_index"]]["id"], "way/80")


if __name__ == "__main__":
    unittest.main()
