extends Node
## Headless gameplay tests. Run:
##   godot --headless --path . res://tests/test_runner.tscn
## Uses the synthetic fixture in tests/fixtures (not real OSM data) for gameplay,
## and checks that every real district scene and data file loads.

var failures := 0
var checks := 0
var level: Node3D
var events: Array = []


func _ready() -> void:
	Game.save_path = "user://test_save.json"
	Game.reset_save()
	await _run()
	print("\n%d checks, %d failures" % [checks, failures])
	DirAccess.remove_absolute(ProjectSettings.globalize_path(Game.save_path))
	get_tree().quit(1 if failures > 0 else 0)


func check(cond: bool, what: String) -> void:
	checks += 1
	if cond:
		print("  ok   ", what)
	else:
		failures += 1
		printerr("  FAIL ", what)


func frames(n: int) -> void:
	for i in n:
		await get_tree().physics_frame


func _run() -> void:
	print("== district data files")
	_test_district_files()
	print("== loading every district scene")
	await _test_district_scenes()
	print("== fixture district")
	var fixture = DistrictRegistry.read_json("res://tests/fixtures/test_district.json")
	level = load("res://scenes/district.tscn").instantiate()
	level.district_override = fixture
	add_child(level)
	await frames(5)
	var m: MissionEngine = level.missions
	m.dialogue_requested.connect(func(npc: String, lines: Array) -> void: events.append(["dialogue", npc, lines]))
	m.mission_completed.connect(func(mi: Dictionary, r: int) -> void: events.append(["completed", mi["id"], r]))
	m.mission_failed.connect(func(mi: Dictionary, reason: String) -> void: events.append(["failed", mi["id"], reason]))
	await _test_world()
	await _test_driving()
	await _test_missions()
	await _test_fuel()
	_test_save()


func _test_district_files() -> void:
	var list := DistrictRegistry.list_districts()
	check(list.size() == 5, "5 districts found (got %d)" % list.size())
	var valid_types := ["drive_to", "delivery", "timed", "speed_limit"]
	for d in list:
		var ms: Array = d.get("missions", [])
		check(ms.size() == 10, "%s has 10 missions" % d["id"])
		var b: Dictionary = d.get("bbox", {})
		var w_km: float = (b["east"] - b["west"]) * 111.32 * cos(deg_to_rad(b["south"]))
		var h_km: float = (b["north"] - b["south"]) * 110.54
		check(w_km > 1.5 and w_km < 3.5 and h_km > 1.5 and h_km < 3.5, "%s bbox is %.1f x %.1f km" % [d["id"], w_km, h_km])
		var ok := true
		for mi in ms:
			for key in ["id", "type", "title", "description", "npc", "dialogue", "reward"]:
				if not mi.has(key):
					ok = false
					printerr("    %s missing %s" % [mi.get("id", "?"), key])
			if not (mi.get("type") in valid_types):
				ok = false
			if mi.get("type") == "delivery" and not (mi.has("pickup") and mi.has("dropoff") and mi.has("item")):
				ok = false
			if mi.get("type") != "delivery" and not mi.has("target"):
				ok = false
			if mi.get("type") == "timed" and not mi.has("time_limit"):
				ok = false
			if mi.get("type") == "speed_limit" and not mi.has("speed_limit_kmh"):
				ok = false
			if mi.get("dialogue", {}).get("intro", []).is_empty() or mi.get("dialogue", {}).get("complete", []).is_empty():
				ok = false
		check(ok, "%s missions have all required fields" % d["id"])


func _test_district_scenes() -> void:
	for d in DistrictRegistry.list_districts():
		var path := DistrictRegistry.scene_path(d["id"])
		check(path.ends_with("/%s.tscn" % d["id"]), "%s has its own scene %s" % [d["id"], path])
		var inst: Node3D = load(path).instantiate()
		add_child(inst)
		await frames(3)
		check(inst.car != null and inst.hud != null and inst.missions != null, "%s scene builds car, HUD and missions" % d["id"])
		if not DistrictRegistry.has_osm_data(d):
			check(inst.load_error.contains("fetch_osm"), "%s without OSM data shows how to fetch it" % d["id"])
		inst.queue_free()
		await frames(2)


func _test_world() -> void:
	check(level.roads.segment_count() == 14, "14 road segments built (got %d)" % level.roads.segment_count())
	check(level.has_node("Buildings") and level.has_node("BuildingCollision"), "building mesh and collision exist")
	check(level.has_node("MajorRoads") and level.has_node("MinorRoads"), "road meshes exist")
	var p: Vector3 = level.car.global_position
	var hit: Dictionary = level.roads.nearest(Vector2(p.x, p.z), 5.0)
	check(not hit.is_empty() and hit["distance"] < 0.5, "car spawns on a road")
	var info: Dictionary = level.roads.nearest(Vector2(0, 300), 10.0)
	check(info.get("maxspeed_kmh") == 40 and str(info.get("name")).begins_with("Test Street"), "road lookup returns name and speed limit")


func _test_driving() -> void:
	var car: Car = level.car
	car.place(Vector3(0, 0, 500), Vector2(0, -1))  # on Test Street, facing north
	await frames(10)
	var start := car.global_position
	var fuel0 := car.fuel.liters
	car.input_override = {"throttle": 1.0}
	await frames(180)
	check(car.speed_kmh() > 40.0, "car accelerates (%.0f km/h after 3 s)" % car.speed_kmh())
	check(car.global_position.z < start.z - 30.0, "car moves forward (north): z %.1f -> %.1f" % [start.z, car.global_position.z])
	check(car.fuel.liters < fuel0, "driving burns fuel")
	check(absf(car.global_position.y) < 1.0, "car stays on the ground (y=%.2f)" % car.global_position.y)
	car.input_override = {"brake": 1.0}
	await frames(150)
	check(car.speed_kmh() < 3.0 or car.speed < 0.0, "car brakes")
	car.input_override = {"throttle": 1.0, "steer": 1.0}
	var yaw0 := car.rotation.y
	await frames(60)
	check(absf(wrapf(car.rotation.y - yaw0, -PI, PI)) > 0.3, "car steers")
	# Drive into Test Block 0 (x 60..140, z 60..140) from the north.
	car.place(Vector3(100, 0, 20), Vector2(0, 1))
	car.input_override = {"throttle": 1.0}
	await frames(180)
	check(car.global_position.z < 60.0, "buildings block the car (z=%.1f)" % car.global_position.z)
	# Station Over Road straddles Test Street (x=0, z -230..-170): the road stays open.
	car.place(Vector3(0, 0, -150), Vector2(0, -1))
	car.input_override = {"throttle": 1.0}
	await frames(300)
	check(car.global_position.z < -240.0, "buildings drawn over a road do not block it (z=%.1f)" % car.global_position.z)
	car.input_override = {"handbrake": true}
	await frames(30)


func _start_and_accept(m: MissionEngine, id: String) -> bool:
	events.clear()
	if not m.start_mission(id):
		return false
	await frames(1)
	# Click through the intro dialogue like a player would.
	for i in 5:
		if m.state != MissionEngine.State.INTRO:
			break
		level.hud._advance_dialogue()
	return m.state == MissionEngine.State.ACTIVE


func _teleport(pos: Vector3) -> void:
	level.car.place(pos, Vector2(0, -1))
	await frames(3)


func _finish_outro(m: MissionEngine) -> void:
	for i in 5:
		if m.state != MissionEngine.State.OUTRO:
			break
		level.hud._advance_dialogue()


func _test_missions() -> void:
	var m: MissionEngine = level.missions
	var car: Car = level.car
	car.input_override = {"handbrake": true}
	check(m.missions.size() == 5, "5 fixture missions loaded")
	check(not m.get_mission("t_unverified")["available"], "mission with unverified place is unavailable")
	check(not m.start_mission("t_unverified"), "unavailable mission cannot start")

	# drive_to
	var money0 := Game.money
	check(await _start_and_accept(m, "t_drive"), "drive_to mission starts after intro dialogue")
	check(events.size() > 0 and events[0][0] == "dialogue" and events[0][2][1] == "Go to North Point", "intro dialogue has the place name")
	check(m.objective_position() != null, "objective has a world position")
	check(m.has_node("ObjectiveMarker"), "objective marker is placed in the world")
	await _teleport(m.objective_position())
	check(events.any(func(e): return e[0] == "completed" and e[1] == "t_drive"), "drive_to completes on arrival")
	check(Game.money == money0 + 100, "reward paid (AED %d -> %d)" % [money0, Game.money])
	check(level.hud._reward_panel.visible, "reward popup shown")
	_finish_outro(m)
	check(m.state == MissionEngine.State.IDLE, "completion dialogue closes")

	# delivery
	check(await _start_and_accept(m, "t_deliver"), "delivery starts")
	check(m.stage == "pickup", "delivery begins at pickup")
	await _teleport(m.objective_position())
	check(m.stage == "dropoff", "pickup reached, now dropoff")
	await _teleport(m.objective_position())
	check(events.any(func(e): return e[0] == "completed" and e[1] == "t_deliver"), "delivery completes at dropoff")
	_finish_outro(m)

	# timed (1 second limit): wait and fail
	await _teleport(Vector3(0, 0, 0))
	check(await _start_and_accept(m, "t_timed"), "timed mission starts")
	check(m.time_left > 0.0, "timer running")
	await frames(90)
	check(events.any(func(e): return e[0] == "failed" and e[1] == "t_timed"), "timed mission fails when time runs out")
	check(m.state == MissionEngine.State.IDLE, "engine idle after failure")

	# speed_limit: speeding fails, careful driving succeeds
	check(await _start_and_accept(m, "t_speed"), "speed-limit mission starts")
	car.place(Vector3(0, 0, 500), Vector2(0, -1))
	car.input_override = {"throttle": 1.0}
	await frames(240)
	check(events.any(func(e): return e[0] == "failed" and e[1] == "t_speed"), "speeding fails the mission")
	car.input_override = {"handbrake": true}
	await frames(60)
	check(await _start_and_accept(m, "t_speed"), "speed-limit mission restarts")
	await _teleport(m.objective_position())
	check(events.any(func(e): return e[0] == "completed" and e[1] == "t_speed"), "speed-limit mission completes under the limit")
	_finish_outro(m)

	# replay pays a reduced reward
	var before := Game.money
	await _start_and_accept(m, "t_drive")
	await _teleport(m.objective_position())
	check(Game.money == before + 25, "replay pays 25%% (got %d)" % (Game.money - before))
	_finish_outro(m)

	# cancel
	await _start_and_accept(m, "t_drive")
	m.cancel_mission()
	check(m.state == MissionEngine.State.IDLE and m.objective_position() == null, "mission can be cancelled")


func _test_fuel() -> void:
	var car: Car = level.car
	car.input_override = {"handbrake": true}
	var station: Vector3 = level.fuel_stations[0]["position"]
	await _teleport(Vector3(station.x, 0, station.z))
	car.fuel.liters = 10.0
	var money0 := Game.money
	level.hud._try_refuel()
	check(car.fuel.liters > 10.0, "refuel adds fuel (%.1f L)" % car.fuel.liters)
	check(Game.money < money0, "refuel costs money (AED %d -> %d)" % [money0, Game.money])
	car.fuel.liters = 0.0
	car.input_override = {"throttle": 1.0}
	await frames(60)
	check(car.speed_kmh() < 1.0, "car cannot accelerate without fuel")
	level.hud._reset_car()
	check(car.fuel.liters >= 5.0, "reset gives emergency fuel")
	car.input_override = {}


func _test_save() -> void:
	var money := Game.money
	Game.money = 0
	Game.load_save()
	check(Game.money == money and money > 0, "money is saved and loaded (AED %d)" % Game.money)
	check(Game.times_completed("t_drive") == 2, "completed missions are saved")
