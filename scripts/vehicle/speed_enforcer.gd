class_name SpeedEnforcer
extends Node
## Speed cameras everywhere: compares the car's speed with the OSM speed limit of
## the road it is on and issues a ticket after it stays more than TOLERANCE_KMH over
## the limit for GRACE seconds. Another ticket follows every COOLDOWN seconds the car
## keeps speeding. Fines follow the UAE federal speeding schedule.

signal ticket_issued(ticket: Dictionary)
signal road_changed(road: Dictionary)

const TOLERANCE_KMH := 5.0
const GRACE := 1.0
const COOLDOWN := 12.0
const QUERY_INTERVAL := 0.1
## [km/h over the limit (up to and including), fine in AED]
const FINES := [[20, 300], [30, 600], [40, 700], [50, 1000], [60, 1500], [80, 2000], [INF, 3000]]

var car: Car
var roads: RoadNetwork
var enabled := true
var road: Dictionary = {}  # current road from RoadNetwork.road_at, {} when off road
var over_time := 0.0
var cooldown := 0.0
var tickets_this_session := 0
var _query_time := 0.0


static func fine_for(excess_kmh: float) -> int:
	for f in FINES:
		if excess_kmh <= float(f[0]):
			return int(f[1])
	return 3000


## Speed limit of the current road in km/h, or -1 when off road / unknown.
func limit_kmh() -> int:
	if road.is_empty() or road.get("maxspeed_kmh") == null:
		return -1
	return int(road["maxspeed_kmh"])


func is_speeding() -> bool:
	var limit := limit_kmh()
	return limit > 0 and car.speed_kmh() > limit + TOLERANCE_KMH


func _physics_process(delta: float) -> void:
	if car == null or roads == null:
		return
	cooldown = maxf(cooldown - delta, 0.0)
	_query_time -= delta
	if _query_time <= 0.0:
		_query_time = QUERY_INTERVAL
		var p := car.global_position
		var f := car.forward()
		var prev := int(road.get("road_id", -1))
		var hit := roads.road_at(Vector2(p.x, p.z), Vector2(f.x, f.z), prev)
		if int(hit.get("road_id", -2)) != prev:
			road_changed.emit(hit)
		road = hit
	if not enabled or not is_speeding():
		over_time = 0.0
		return
	over_time += delta
	if over_time >= GRACE and cooldown <= 0.0:
		_issue_ticket()


func _issue_ticket() -> void:
	var limit := limit_kmh()
	var speed := car.speed_kmh()
	var excess := speed - limit
	var fine := fine_for(excess)
	var road_name: String = road.get("name", "")
	if road_name.is_empty():
		road_name = "unnamed %s road" % road.get("highway", "")
	var ticket := {"road": road_name, "limit": limit, "speed": int(round(speed)), "fine": fine}
	Game.pay_fine(fine, ticket)
	tickets_this_session += 1
	cooldown = COOLDOWN
	over_time = 0.0
	ticket_issued.emit(ticket)
