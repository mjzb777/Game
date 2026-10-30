# Dubai Drive

A driving game in Godot 4 (GDScript) set in five real Dubai districts built from
OpenStreetMap data: Mirdif, Downtown, Jumeirah, Umm Suqeim and Oud Metha.

Everything on the map comes from OpenStreetMap: roads (name, type, speed limit, lanes,
one-way, bridges and tunnels), building footprints and heights, petrol stations,
roundabouts, traffic signals and named places. Every mission place is a real named OSM
place in that district, and every time limit comes from the real road route. There is no
traffic and no visual polish yet.

## Quick start

Requirements: Godot **4.5** (4.3+ should work) and Python 3.8+ (standard library only).

```bash
# 1. Download OSM data for all 5 districts (Overpass; mirrors are tried in turn)
python3 tools/fetch_osm.py

# 2. Match mission places, put them on roads, route them, set time limits and spawns
python3 tools/resolve_missions.py

# 3. Run the game
godot --path .
```

The converted data in `data/osm/*.json` is committed, so step 1 is only needed to refresh
it. `fetch_osm.py` tries overpass-api.de, lz4.overpass-api.de, overpass.kumi.systems,
overpass.private.coffee and maps.mail.ru (`--rounds` passes, `--endpoint` to choose).
If all of them fail it reads the same data from the official OSM API
(api.openstreetmap.org) in tiles; `--osm-api` goes there directly. It never makes data up.
`--from-raw` rebuilds `data/osm/` from the raw downloads in `data/osm_raw/` (not committed).

It prints a log per district: how many roads got a default speed limit (and which), how
many building heights were guessed, polygon repairs, and the size of the drivable network.

Open `data/place_verification.md` after step 2. It lists every mission place, the OSM
element it matched (id and coordinates), the road point the marker uses, and each mission's
route, distance and time limit. `resolve_missions.py` exits non-zero if any place is
missing, off-road or unreachable.

## Controls

| Key | Action |
|---|---|
| W / Up | Accelerate |
| S / Down | Brake, then reverse |
| A, D / Left, Right | Steer |
| Space | Handbrake |
| M or Tab | Mission list (start or cancel a mission) |
| Enter / E | Advance NPC dialogue |
| F | Refuel (stop at the yellow pump of a real petrol station) |
| R | Put the car back on the nearest public road, facing a legal direction. With an empty tank: tow to the nearest real petrol station (AED 150) |
| Esc | Pause / main menu |

## What to test

1. **Menu.** All 5 districts are listed with "10/10 missions". Pick one; only that district loads.
2. **World.** Roads are dark ribbons (major roads darker). Bridges are lighter and drawn on
   top, and tunnels are darker and drawn underneath. Buildings are gray blocks with OSM
   heights (Burj Khalifa is 828 m), and roundabouts have green islands. Traffic signals are
   black poles, and petrol stations are red blocks with a yellow pump on the forecourt road.
   Compare the layout with a real map; north is "up" (-Z).
3. **Spawn.** The car starts on a named real road (see `spawn.road` in the district
   file), lined up with it, facing the legal way on a one-way road.
4. **Driving and speed limits.** The road name and its OSM speed limit are bottom left
   (bridges and tunnels are marked). The speedometer turns red more than 5 km/h over the
   limit. After 1 s over, you get a ticket (UAE fines: AED 300 up to 20 km/h over, 600, 700,
   1000, 1500, 2000, 3000 over 80 km/h over), then another every 12 s you keep speeding.
   Fines can take the wallet negative.
5. **Fuel.** The tank drains faster at high speed, under hard throttle, and slowly at
   idle. Refuelling only works at the pump of a real OSM petrol station (F, AED 3/L, needs
   a positive balance). With an empty tank, R tows you to the nearest station for AED 150.
6. **Missions.** Press M and start one. First drive to the green marker to meet the NPC
   (no timer). The intro dialogue plays, then the leg starts: yellow beam, GPS arrow and
   distance at the top, timer on timed and delivery missions (red under 10 s). On
   speed-limit missions, going over the mission's limit for 1 s fails the mission.
7. **Saving.** Money, "[done]" marks and tickets persist (`user://save.json`). Replaying
   a mission pays 25% of the reward.
8. **Failure cases.** Let a timer run out, speed on a speed-limit mission, cancel a
   mission from the list, run out of fuel, then press R.

## Automated checks

```bash
godot --headless --path . --import                              # first time only
godot --headless --path . -s res://tests/check_scripts.gd      # every script compiles
godot --headless --path . res://tests/test_runner.tscn         # gameplay checks
python3 tests/test_tools.py                                    # OSM conversion, matching, routing
godot --headless --path . res://tests/test_runner.tscn         # 75 gameplay checks
godot --headless --path . -s res://tests/test_menu.gd          # clicks every district button
python3 tests/test_tools.py                                    # OSM conversion + place matching
```

`test_runner.tscn` loads every real district scene and data file. It checks that each has
a real road network, buildings, petrol stations, 10 available missions and a car on a road.
It drives the car, runs all four mission types, and tests tickets, fuel, towing and saving
on a **synthetic** test map in `tests/fixtures/`, which is test data only and not real
OSM data.

## Project layout

```
data/districts/<id>.json   district data: name, bbox, spawn, missions   <- edit this
data/osm/<id>.json         generated by tools/fetch_osm.py
data/place_verification.md generated by tools/resolve_missions.py
scenes/main_menu.tscn      district selector (reads data/districts/)
scenes/districts/<id>.tscn one scene per district (just sets district_id)
scenes/district.tscn       generic district scene for districts without their own scene
scripts/core/              Game autoload (money, save, input), DistrictRegistry, Geo projection
scripts/world/             district.gd (scene root), DistrictBuilder, RoadNetwork
scripts/vehicle/           Car controller, FuelSystem, SpeedEnforcer (tickets), ChaseCamera
scripts/missions/          MissionEngine
scripts/ui/                HUD (speedometer, GPS arrow, timer, dialogue, reward, mission list), main menu
tools/                     fetch_osm.py, resolve_missions.py
tests/                     headless tests + synthetic fixtures
```

## Adding a district (data only)

1. Add `data/districts/<id>.json` with `id`, `name`, `bbox` (south/west/north/east,
   about 2–3.5 km), `spawn` (lat, lon, heading_deg: a hint), `building_defaults` (see
   below) and `missions`. Make sure the bbox contains at least one petrol station.
2. Run `python3 tools/fetch_osm.py <id>`, then `python3 tools/resolve_missions.py`.
   The spawn is moved onto the nearest suitable named road.

The menu finds the new file on its own. A scene in `scenes/districts/<id>.tscn` is
optional; without one, the generic `scenes/district.tscn` is used.

## Mission format

```json
{
  "id": "dt_02", "type": "delivery",
  "title": "Opera Night", "description": "Collect {item} at {pickup} and deliver it to {dropoff} within {time} s.",
  "npc": "Khalid",
  "dialogue": {"intro": ["..."], "complete": ["..."]},
  "reward": 400,
  "item": "a silk shawl",
  "pickup":  {"names": ["The Dubai Mall"], "categories": ["shop=mall"]},
  "dropoff": {"names": ["Dubai Opera"]}
}
```

- **Types.** Every mission starts by meeting the NPC (no timer): at `start` for
  `drive_to` (→ `target`), `timed` (→ `target` before the time runs out) and
  `speed_limit` (→ `target` under `speed_limit_kmh`), and at `pickup` for `delivery`
  (→ `dropoff`, timed).
- **Places.** Each place lists the names it is known by (`categories` only break ties).
  `resolve_missions.py` looks them up in the OSM data, by name only, and writes
  `"resolved": {osm_id, name, lat, lon, access}`. `access` is the point on the nearest
  drivable road where the marker goes. It is on the main road network, never on a
  motorway, slip road, bridge or tunnel, and never across a motorway from the place.
  Coordinates always come from OSM and are never typed in by hand. A place that cannot be
  found, or has no reachable road, is an error: replace the mission with a place that is
  in the data (`--list-pois <district> --grep <text>` to browse).
- **Routes and time limits.** The tool writes `"route"` (fastest legal route along the OSM
  road network at the OSM speed limits: distance, free-flow time, signals, roads used)
  and, for `timed` and `delivery` missions, `"time_limit"`. Timed missions get the
  free-flow time × 1.35 + 15 s; deliveries get × 1.6 + 25 s. Both are rounded up to 5 s.
  Do not edit `time_limit` by hand: it is recomputed.
- **Text.** `{start}`, `{target}`, `{pickup}` and `{dropoff}` are replaced by the real
  OSM names. `{item}`, `{npc}`, `{time}`, `{limit}` and `{km}` are also available.

## Data notes

- Map data © OpenStreetMap contributors, ODbL. The OSM snapshot date is in each
  `data/osm/<id>.json` (`osm_base`).
- **Speed limits.** The OSM `maxspeed` is used where present. Otherwise the default for
  the road type applies: residential 40, arterial (secondary, tertiary) 60, major road
  (primary, trunk) 80, motorway 120. Slip roads get the class below their parent, and
  service roads 25 and living streets 20. Each road has `maxspeed_source` (`osm` or
  `default`), and the per-district counts are in `stats`.
- **Building heights.** `height` (or `building:height`, `est_height`, or `maxheight` on a
  building outline, as on Burj Khalifa) is used first, then `building:levels` × 3.2 m,
  then a guess from `building_defaults` in the district file. Known kinds (house,
  garage, mosque, school...) get typical heights. Other footprints under 400 m² are
  low-rise, mid-size footprints get the district height (45 m in Downtown, villa height
  in Mirdif, Jumeirah and Umm Suqeim), and footprints over 3000 m² are podiums and malls.
  Each building has `height_source` (`osm`, `levels` or `default`).
- **Geometry fixes.** Duplicate points are removed, self-intersecting footprints are
  replaced by their convex hull, and all footprints are wound the same way. Polygons over
  400,000 m² or 1.5 km across (site outlines) and under 4 m² are dropped, as are
  `building=bridge` outlines. Heights are clamped to 830 m, and an inverted or empty
  `min_height` is ignored. Raised structures, canopies and buildings drawn over a road
  are not solid. Footprints the engine cannot triangulate get a roof over their hull.
- **Road network.** One-way roads (including `oneway=-1`, roundabouts and motorways) are
  stored in driving order. `connected` marks roads in the largest strongly connected
  network: you can drive onto them and back off again inside the map. Roads cut off by
  the map edge, exit-only stubs and private roads are drawn, but the car is never spawned
  or reset onto them and no marker is put on them. Bridges and tunnels are flagged with
  their `layer`. In this flat world they are stacked visually, and the HUD and speed
  cameras follow the road the car is driving along, not the one underneath.
- **Petrol stations.** Each station has an `access` point on its forecourt road (a road on
  the same side of any motorway); refuelling happens there.
- The district bounding boxes were set by hand around each district centre. Downtown's
  was moved north-west to include real petrol stations (its 3 × 3 km box had none).
- To export the game, add `*.json` to the export preset's non-resource filter so the
  data files are included.
