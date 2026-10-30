extends Node3D
## Root of a district scene. Reads the district's data file, builds the map from its
## OSM JSON, then adds the car, camera, mission engine and HUD.
## Every district uses this same script; only district_id differs.

@export var district_id := ""

## Tests can inject district data instead of reading data/districts/<id>.json.
var district_override: Dictionary = {}

var district: Dictionary
var geo: Geo
var builder: DistrictBuilder
var roads: RoadNetwork
var fuel_stations: Array = []
var car: Car
var camera: ChaseCamera
var missions: MissionEngine
var enforcer: SpeedEnforcer
var hud: Hud
var load_error := ""


func _ready() -> void:
	if district_id.is_empty():
		district_id = Game.current_district_id
	district = district_override if not district_override.is_empty() else DistrictRegistry.load_district(district_id)
	if district.is_empty():
		load_error = "Unknown district '%s'" % district_id
		push_error(load_error)
		district = {"id": district_id, "name": district_id, "missions": []}

	_add_environment()

	var osm = DistrictRegistry.read_json(DistrictRegistry.osm_path(district))
	var origin := _origin(osm)
	geo = Geo.new(origin.x, origin.y)
	builder = DistrictBuilder.new(geo)
	if osm is Dictionary:
		builder.build(self, osm)
	else:
		load_error = "No map data for %s. Run: python3 tools/fetch_osm.py %s" % [district.get("name", district_id), district.get("id", district_id)]
		push_warning(load_error)
		builder.build(self, {})
	roads = builder.roads
	fuel_stations = builder.fuel_stations

	car = Car.new()
	car.name = "Car"
	add_child(car)
	_place_at_spawn()

	camera = ChaseCamera.new()
	camera.target = car
	add_child(camera)
	camera.make_current()

	missions = MissionEngine.new()
	missions.name = "Missions"
	add_child(missions)
	missions.setup(district, geo, roads, car)

	enforcer = SpeedEnforcer.new()
	enforcer.name = "SpeedEnforcer"
	enforcer.car = car
	enforcer.roads = roads
	add_child(enforcer)

	hud = Hud.new()
	hud.car = car
	hud.engine = missions
	hud.roads = roads
	hud.enforcer = enforcer
	hud.district_name = str(district.get("name", district_id))
	add_child(hud)
	if not load_error.is_empty():
		hud.call_deferred("_toast", load_error)


## Projection origin: the OSM file's origin, else the bbox centre, else the spawn point.
func _origin(osm: Variant) -> Vector2:
	if osm is Dictionary and osm.has("origin"):
		return Vector2(osm["origin"]["lat"], osm["origin"]["lon"])
	var b: Dictionary = district.get("bbox", {})
	if not b.is_empty():
		return Vector2((b["south"] + b["north"]) * 0.5, (b["west"] + b["east"]) * 0.5)
	var s: Dictionary = district.get("spawn", {"lat": 0.0, "lon": 0.0})
	return Vector2(s["lat"], s["lon"])


## The spawn in the data file is already on a real road (tools/resolve_missions.py);
## snapping again to the nearest drivable road keeps old or hand-edited files safe.
## One-way roads always face their legal direction.
func _place_at_spawn() -> void:
	var s: Dictionary = district.get("spawn", {})
	var pos := geo.to_local(s.get("lat", geo.origin_lat), s.get("lon", geo.origin_lon))
	var heading := deg_to_rad(float(s.get("heading_deg", 0.0)))
	var dir := Vector2(sin(heading), -cos(heading))  # 0 deg = north
	var hit := roads.nearest(Vector2(pos.x, pos.z), 1500.0, true)
	if hit.is_empty():
		hit = roads.nearest(Vector2(pos.x, pos.z), 1500.0)
	if not hit.is_empty():
		var q: Vector2 = hit["point"]
		pos = Vector3(q.x, 0, q.y)
		var road_dir: Vector2 = hit["dir"]
		dir = road_dir if hit["oneway"] or road_dir.dot(dir) >= 0.0 else -road_dir
	car.place(pos, dir)


func _add_environment() -> void:
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-55, -35, 0)
	sun.shadow_enabled = false
	add_child(sun)
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.62, 0.78, 0.95)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.8, 0.8, 0.8)
	env.ambient_light_energy = 0.6
	var we := WorldEnvironment.new()
	we.environment = env
	add_child(we)
