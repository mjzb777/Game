class_name Geo
extends RefCounted
## Projects lat/lon onto a flat local plane in metres around an origin.
## +X is east, -Z is north (Godot's forward), so a map reads naturally from above.

const M_PER_DEG_LAT := 110540.0
const M_PER_DEG_LON_EQUATOR := 111320.0

var origin_lat: float
var origin_lon: float
var _m_per_deg_lon: float


func _init(lat: float, lon: float) -> void:
	origin_lat = lat
	origin_lon = lon
	_m_per_deg_lon = M_PER_DEG_LON_EQUATOR * cos(deg_to_rad(lat))


func to_local2(lat: float, lon: float) -> Vector2:
	return Vector2((lon - origin_lon) * _m_per_deg_lon, -(lat - origin_lat) * M_PER_DEG_LAT)


func to_local(lat: float, lon: float, y: float = 0.0) -> Vector3:
	var p := to_local2(lat, lon)
	return Vector3(p.x, y, p.y)
