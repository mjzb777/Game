class_name RoadNetwork
extends RefCounted
## Road segments in local metres with a grid index for "nearest road" lookups
## (spawn snapping, mission target snapping, HUD road name / speed limit).

const CELL := 50.0

var roads: Array = []  # [{name, highway, maxspeed_kmh}]
var _seg_a := PackedVector2Array()
var _seg_b := PackedVector2Array()
var _seg_road := PackedInt32Array()
var _grid: Dictionary = {}  # Vector2i -> PackedInt32Array of segment ids


func add_road(info: Dictionary, points: PackedVector2Array) -> void:
	var road_id := roads.size()
	roads.append(info)
	for i in points.size() - 1:
		var seg_id := _seg_a.size()
		_seg_a.append(points[i])
		_seg_b.append(points[i + 1])
		_seg_road.append(road_id)
		var lo := Vector2i((points[i].min(points[i + 1]) / CELL).floor())
		var hi := Vector2i((points[i].max(points[i + 1]) / CELL).floor())
		for cx in range(lo.x, hi.x + 1):
			for cy in range(lo.y, hi.y + 1):
				var key := Vector2i(cx, cy)
				if not _grid.has(key):
					_grid[key] = PackedInt32Array()
				_grid[key].append(seg_id)


func segment_count() -> int:
	return _seg_a.size()


## Returns {} if no road is within max_dist, else
## {point: Vector2, dir: Vector2, distance: float, name: String, highway: String, maxspeed_kmh}
func nearest(pos: Vector2, max_dist: float = 200.0) -> Dictionary:
	var best_d := INF
	var best_seg := -1
	var best_pt := Vector2.ZERO
	var r := int(ceil(max_dist / CELL))
	var c := Vector2i((pos / CELL).floor())
	var seen := {}
	for cx in range(c.x - r, c.x + r + 1):
		for cy in range(c.y - r, c.y + r + 1):
			var key := Vector2i(cx, cy)
			if not _grid.has(key):
				continue
			for s in _grid[key]:
				if seen.has(s):
					continue
				seen[s] = true
				var pt := Geometry2D.get_closest_point_to_segment(pos, _seg_a[s], _seg_b[s])
				var d := pt.distance_to(pos)
				if d < best_d:
					best_d = d
					best_seg = s
					best_pt = pt
	if best_seg < 0 or best_d > max_dist:
		return {}
	var info: Dictionary = roads[_seg_road[best_seg]]
	return {
		"point": best_pt,
		"dir": (_seg_b[best_seg] - _seg_a[best_seg]).normalized(),
		"distance": best_d,
		"name": info.get("name", ""),
		"highway": info.get("highway", ""),
		"maxspeed_kmh": info.get("maxspeed_kmh"),
	}


## True if any road segment enters the polygon (used so buildings never block a road).
func crosses_polygon(poly: PackedVector2Array) -> bool:
	var lo := poly[0]
	var hi := poly[0]
	for p in poly:
		lo = lo.min(p)
		hi = hi.max(p)
	var c0 := Vector2i((lo / CELL).floor())
	var c1 := Vector2i((hi / CELL).floor())
	for cx in range(c0.x, c1.x + 1):
		for cy in range(c0.y, c1.y + 1):
			var key := Vector2i(cx, cy)
			if not _grid.has(key):
				continue
			for s in _grid[key]:
				var a := _seg_a[s]
				var b := _seg_b[s]
				if Geometry2D.is_point_in_polygon(a, poly) or Geometry2D.is_point_in_polygon(b, poly):
					return true
				for i in poly.size():
					if Geometry2D.segment_intersects_segment(a, b, poly[i], poly[(i + 1) % poly.size()]) != null:
						return true
	return false
