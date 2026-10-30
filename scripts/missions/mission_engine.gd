class_name MissionEngine
extends Node
## Runs missions defined in a district's data file.
##
## Every mission starts by meeting the NPC at a real place (TO_START, no timer):
## "start" for most missions, the "pickup" place for deliveries. Then the intro
## dialogue plays and the timed/rated leg begins:
##   drive_to     - start -> "target"
##   delivery     - pickup -> "dropoff" (carrying "item")
##   timed        - start -> "target" before "time_limit" seconds run out
##   speed_limit  - start -> "target" without exceeding "speed_limit_kmh"
## time_limit comes from tools/resolve_missions.py (route length and OSM speed
## limits). Places are only usable once that tool has matched them to real OSM data
## ("resolved"); markers go on the road access point it computed.

signal dialogue_requested(npc: String, lines: Array)
signal mission_started(mission: Dictionary)
signal objective_changed(text: String)
signal mission_completed(mission: Dictionary, reward: int)
signal mission_failed(mission: Dictionary, reason: String)

enum State { IDLE, TO_START, INTRO, ACTIVE, OUTRO }

const REACH_RADIUS := 15.0
const SNAP_TO_ROAD_DIST := 300.0
const SPEED_TOLERANCE_KMH := 2.0
const SPEEDING_GRACE := 1.0  # seconds over the limit before failing
const REPLAY_REWARD_FACTOR := 0.25

var missions: Array = []  # prepared mission dictionaries
var state := State.IDLE
var current: Dictionary = {}
var stage := ""  # "start", "pickup", "dropoff" or "target"
var time_left := -1.0
var speeding_time := 0.0

var _car: Car
var _geo: Geo
var _roads: RoadNetwork
var _marker: MeshInstance3D


func setup(district: Dictionary, geo: Geo, roads: RoadNetwork, car: Car) -> void:
	_geo = geo
	_roads = roads
	_car = car
	missions.clear()
	for m in district.get("missions", []):
		missions.append(_prepare(m))


func _prepare(src: Dictionary) -> Dictionary:
	var m := src.duplicate(true)
	m["positions"] = {}
	m["place_names"] = {}
	m["available"] = true
	m["unavailable_reason"] = ""
	for role in ["start", "pickup", "dropoff", "target"]:
		if not m.has(role):
			continue
		var res = m[role].get("resolved")
		if not (res is Dictionary) or not res.has("lat"):
			m["available"] = false
			m["unavailable_reason"] = "place not verified in OSM data"
			continue
		var access = res.get("access")
		if access is Dictionary:
			m["positions"][role] = _geo.to_local(access["lat"], access["lon"])
		else:
			m["positions"][role] = _snap_to_road(_geo.to_local(res["lat"], res["lon"]))
		m["place_names"][role] = str(res.get("name", ""))
	# Where the NPC waits: the pickup for deliveries, else "start" (if the mission has one).
	m["start_role"] = "pickup" if str(m.get("type", "")) == "delivery" else ("start" if m["positions"].has("start") else "")
	var t := str(m.get("type", ""))
	var needed := ["pickup", "dropoff"] if t == "delivery" else ["target"]
	for role in needed:
		if not m.has(role):
			m["available"] = false
			m["unavailable_reason"] = "missing %s" % role
	if t == "timed" and float(m.get("time_limit", 0)) <= 0.0:
		m["available"] = false
		m["unavailable_reason"] = "timed mission without time_limit"
	if t == "speed_limit" and float(m.get("speed_limit_kmh", 0)) <= 0.0:
		m["available"] = false
		m["unavailable_reason"] = "speed_limit mission without speed_limit_kmh"
	return m


## Places can be inside a building footprint, so the marker goes on the nearest road.
## (Only for data without a precomputed access point.)
func _snap_to_road(p: Vector3) -> Vector3:
	var hit := _roads.nearest(Vector2(p.x, p.z), SNAP_TO_ROAD_DIST, true)
	if hit.is_empty():
		return p
	var q: Vector2 = hit["point"]
	return Vector3(q.x, 0, q.y)


func format_text(m: Dictionary, text: String) -> String:
	var names: Dictionary = m.get("place_names", {})
	return text.format({
		"start": names.get("start", "?"),
		"npc": str(m.get("npc", "")),
		"km": "%.1f" % (float(m.get("route", {}).get("distance_m", 0)) / 1000.0),
		"target": names.get("target", "?"),
		"pickup": names.get("pickup", "?"),
		"dropoff": names.get("dropoff", "?"),
		"item": str(m.get("item", "the package")),
		"limit": str(int(m.get("speed_limit_kmh", 0))),
		"time": str(int(m.get("time_limit", 0))),
	})


func format_lines(m: Dictionary, lines: Array) -> Array:
	var out: Array = []
	for l in lines:
		out.append(format_text(m, str(l)))
	return out


func get_mission(mission_id: String) -> Dictionary:
	for m in missions:
		if m["id"] == mission_id:
			return m
	return {}


func is_busy() -> bool:
	return state != State.IDLE


func start_mission(mission_id: String) -> bool:
	if is_busy():
		return false
	var m := get_mission(mission_id)
	if m.is_empty() or not m["available"]:
		return false
	current = m
	var start_role := str(m.get("start_role", ""))
	if start_role.is_empty():
		_play_intro()
		return true
	# Drive to the NPC first; the clock only starts once the intro dialogue is over.
	state = State.TO_START
	stage = start_role
	time_left = -1.0
	speeding_time = 0.0
	_show_marker()
	mission_started.emit(current)
	objective_changed.emit(objective_text())
	return true


func _play_intro() -> void:
	state = State.INTRO
	_hide_marker()
	objective_changed.emit("")
	dialogue_requested.emit(str(current.get("npc", "")), format_lines(current, current.get("dialogue", {}).get("intro", [])))


## Called by the UI when the player closes a dialogue.
func dialogue_finished() -> void:
	if state == State.INTRO:
		_begin()
	elif state == State.OUTRO:
		state = State.IDLE
		current = {}


func _begin() -> void:
	var was_met := not str(current.get("start_role", "")).is_empty()
	state = State.ACTIVE
	if current.get("type") == "delivery":
		stage = "dropoff" if was_met else "pickup"
	else:
		stage = "target"
	time_left = float(current.get("time_limit", -1.0))
	if time_left <= 0.0:
		time_left = -1.0
	speeding_time = 0.0
	_show_marker()
	mission_started.emit(current)
	objective_changed.emit(objective_text())


func cancel_mission() -> void:
	if state == State.ACTIVE or state == State.INTRO or state == State.TO_START:
		_fail("Mission cancelled")


func objective_text() -> String:
	var names: Dictionary = current.get("place_names", {})
	if state == State.TO_START:
		if stage == "pickup":
			return "Collect %s from %s at %s" % [current.get("item", "the package"), current.get("npc", "?"), names.get("pickup", "?")]
		return "Meet %s at %s" % [current.get("npc", "?"), names.get("start", "?")]
	if state != State.ACTIVE:
		return ""
	var text := ""
	match stage:
		"pickup":
			text = "Pick up %s at %s" % [current.get("item", "the package"), names.get("pickup", "?")]
		"dropoff":
			text = "Deliver %s to %s" % [current.get("item", "the package"), names.get("dropoff", "?")]
		_:
			text = "Drive to %s" % names.get("target", "?")
	if current.get("type") == "speed_limit":
		text += "  (max %d km/h)" % int(current["speed_limit_kmh"])
	return text


## World position of the current objective, or null when no mission is active.
func objective_position() -> Variant:
	if state != State.ACTIVE and state != State.TO_START:
		return null
	return current["positions"].get(stage)


func _physics_process(delta: float) -> void:
	if _car == null:
		return
	if state == State.TO_START:
		var meet: Vector3 = current["positions"][stage]
		var cp := _car.global_position
		if Vector2(cp.x - meet.x, cp.z - meet.z).length() <= REACH_RADIUS:
			_play_intro()
		return
	if state != State.ACTIVE:
		return
	if time_left > 0.0:
		time_left -= delta
		if time_left <= 0.0:
			time_left = 0.0
			_fail("Time is up!")
			return
	if current.get("type") == "speed_limit":
		if _car.speed_kmh() > float(current["speed_limit_kmh"]) + SPEED_TOLERANCE_KMH:
			speeding_time += delta
			if speeding_time >= SPEEDING_GRACE:
				_fail("You went over %d km/h!" % int(current["speed_limit_kmh"]))
				return
		else:
			speeding_time = 0.0
	var target: Vector3 = current["positions"][stage]
	var car_pos := _car.global_position
	if Vector2(car_pos.x - target.x, car_pos.z - target.z).length() <= REACH_RADIUS:
		_reached()


func _reached() -> void:
	if stage == "pickup":
		stage = "dropoff"
		_show_marker()
		objective_changed.emit(objective_text())
		return
	_complete()


func _complete() -> void:
	var m := current
	var reward := int(m.get("reward", 0))
	if Game.times_completed(m["id"]) > 0:
		reward = int(reward * REPLAY_REWARD_FACTOR)
	Game.mark_completed(m["id"])
	Game.add_money(reward)
	_hide_marker()
	state = State.OUTRO
	time_left = -1.0
	mission_completed.emit(m, reward)
	objective_changed.emit("")
	dialogue_requested.emit(str(m.get("npc", "")), format_lines(m, m.get("dialogue", {}).get("complete", [])))


func _fail(reason: String) -> void:
	var m := current
	_hide_marker()
	state = State.IDLE
	current = {}
	time_left = -1.0
	mission_failed.emit(m, reason)
	objective_changed.emit("")


func _show_marker() -> void:
	_hide_marker()
	var mesh := CylinderMesh.new()
	mesh.top_radius = REACH_RADIUS * 0.6
	mesh.bottom_radius = REACH_RADIUS * 0.6
	mesh.height = 80.0
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	if state == State.TO_START:
		mat.albedo_color = Color(0.2, 0.9, 0.4, 0.35)  # green: meet the NPC
	elif stage == "pickup":
		mat.albedo_color = Color(0.2, 0.6, 1.0, 0.35)
	else:
		mat.albedo_color = Color(1.0, 0.85, 0.1, 0.35)
	mesh.material = mat
	_marker = MeshInstance3D.new()
	_marker.name = "ObjectiveMarker"
	_marker.mesh = mesh
	add_child(_marker)
	_marker.global_position = current["positions"][stage] + Vector3(0, mesh.height * 0.5, 0)


func _hide_marker() -> void:
	if is_instance_valid(_marker):
		_marker.queue_free()
	_marker = null
