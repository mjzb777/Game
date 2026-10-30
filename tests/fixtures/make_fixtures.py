#!/usr/bin/env python3
"""Writes SYNTHETIC test data (not real OSM) used only by tests/test_runner.gd:
a 1.2 km road grid with a few buildings, one petrol station and missions of every type."""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
LAT0, LON0 = 25.2, 55.3
M_LAT, M_LON = 110540.0, 111320.0 * 0.904827  # cos(25.2 deg)


def ll(x, z):  # local metres -> [lat, lon]; -z is north
    return [round(LAT0 - z / M_LAT, 7), round(LON0 + x / M_LON, 7)]


roads = []
for i, c in enumerate(range(-600, 601, 200)):
    roads.append({"id": "way/%d" % (100 + i), "name": "Test Street %d" % i, "highway": "residential",
                  "maxspeed_kmh": 40, "lanes": None, "oneway": False, "connected": True,
                  "points": [ll(c, -600), ll(c, 600)]})
    roads.append({"id": "way/%d" % (200 + i), "name": "Test Avenue %d" % i, "highway": "primary",
                  "maxspeed_kmh": 80, "lanes": 4, "oneway": False, "connected": True,
                  "points": [ll(-600, c), ll(600, c)]})
# A disconnected private lane next to the spawn: never used for spawning or resets.
roads.append({"id": "way/900", "name": "Private Lane", "highway": "service", "maxspeed_kmh": 25,
              "lanes": None, "oneway": False, "connected": False, "private": True,
              "points": [ll(5, -50), ll(5, 50)]})
buildings = []
for i, (x, z) in enumerate([(100, 100), (-100, 100), (100, -100), (300, 300)]):
    s = 40
    buildings.append({"id": "way/%d" % (300 + i), "name": "Test Block %d" % i, "height": None, "levels": 5,
                      "points": [ll(x - s, z - s), ll(x + s, z - s), ll(x + s, z + s), ll(x - s, z + s)]})
# Drawn over Test Street at x=0 (like an elevated metro station): must not block the road.
buildings.append({"id": "way/399", "name": "Station Over Road", "kind": "train_station", "height": 12, "levels": None,
                  "points": [ll(-20, -230), ll(20, -230), ll(20, -170), ll(-20, -170)]})
osm = {"district": "test", "source": "synthetic test fixture", "bbox": {
           "south": ll(0, 650)[0], "north": ll(0, -650)[0], "west": ll(-650, 0)[1], "east": ll(650, 0)[1]},
       "origin": {"lat": LAT0, "lon": LON0}, "roads": roads, "buildings": buildings,
       "fuel": [{"id": "node/1", "name": "Test Fuel", "brand": "", "lat": ll(210, 30)[0], "lon": ll(210, 30)[1],
                 "access": {"lat": ll(210, 0)[0], "lon": ll(210, 0)[1], "road": "Test Avenue 3", "snap_m": 30}}],
       "pois": [{"id": "node/2", "name": "Test Mall", "name_en": "", "category": "shop=mall",
                 "lat": ll(200, 400)[0], "lon": ll(200, 400)[1]}]}


def place(x, z, name):
    lat, lon = ll(x, z)
    return {"names": [name], "resolved": {"osm_id": "node/0", "name": name, "lat": lat, "lon": lon,
                                          "access": {"lat": lat, "lon": lon, "road": "test", "snap_m": 0}}}


def mission(mid, mtype, reward, **kw):
    m = {"id": mid, "type": mtype, "title": mid, "description": "Test {target}", "npc": "Tester",
         "dialogue": {"intro": ["Hello", "Go to {target}"], "complete": ["Done"]}, "reward": reward}
    m.update(kw)
    return m


district = {"id": "test", "name": "Test District", "bbox": osm["bbox"],
            "spawn": {"lat": LAT0, "lon": LON0, "heading_deg": 0},
            "osm_file": "res://tests/fixtures/test_osm.json",
            "missions": [
                mission("t_drive", "drive_to", 100, start=place(0, 200, "Meeting Point"),
                        target=place(0, -400, "North Point")),
                mission("t_deliver", "delivery", 200, item="a box", pickup=place(200, 0, "Pickup Place"),
                        dropoff=place(-200, 200, "Dropoff Place"), time_limit=60),
                mission("t_timed", "timed", 300, time_limit=1, start=place(0, 200, "Meeting Point"),
                        target=place(600, 600, "Far Corner")),
                mission("t_speed", "speed_limit", 400, speed_limit_kmh=40, start=place(0, 400, "South Gate"),
                        target=place(400, 0, "Slow Zone")),
                mission("t_unverified", "drive_to", 50, start=place(0, 200, "Meeting Point"),
                        target={"names": ["Nowhere"]}),
            ]}
with open(os.path.join(HERE, "test_osm.json"), "w") as f:
    json.dump(osm, f, indent=1)
with open(os.path.join(HERE, "test_district.json"), "w") as f:
    json.dump(district, f, indent=1)
print("fixtures written")
