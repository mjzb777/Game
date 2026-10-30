class_name DistrictBuilder
extends RefCounted
## Builds a district from OSM JSON (tools/fetch_osm.py output):
## ground plane, flat road ribbons, extruded gray buildings, fuel stations.

const ROAD_WIDTH := {
	"motorway": 16.0, "trunk": 14.0, "primary": 12.0, "secondary": 10.0, "tertiary": 8.0,
	"motorway_link": 7.0, "trunk_link": 7.0, "primary_link": 7.0, "secondary_link": 6.0,
	"tertiary_link": 6.0, "residential": 6.0, "unclassified": 6.0, "living_street": 5.0,
	"service": 4.0, "road": 6.0,
}
const MAJOR := ["motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link", "primary_link"]
const LEVEL_HEIGHT := 3.2
## Building kinds the car can drive under (canopies, bridges).
const NO_COLLISION_KINDS := ["roof", "canopy", "carport", "bridge"]
const FUEL_STATION_RADIUS := 18.0
const EDGE_MARGIN := 150.0

var geo: Geo
var roads := RoadNetwork.new()
var fuel_stations: Array = []  # [{name, position: Vector3}]
var half_extent := Vector2(1500, 1500)


func _init(p_geo: Geo) -> void:
	geo = p_geo


func build(parent: Node3D, osm: Dictionary) -> void:
	var b: Dictionary = osm.get("bbox", {})
	if not b.is_empty():
		var ne := geo.to_local2(b["north"], b["east"])
		var sw := geo.to_local2(b["south"], b["west"])
		half_extent = Vector2(maxf(absf(ne.x), absf(sw.x)), maxf(absf(ne.y), absf(sw.y))) + Vector2.ONE * EDGE_MARGIN
	_build_ground(parent)
	_build_roads(parent, osm.get("roads", []))
	_build_buildings(parent, osm.get("buildings", []))
	_build_fuel(parent, osm.get("fuel", []))


func _material(color: Color) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = color
	return m


func _build_ground(parent: Node3D) -> void:
	var mesh := PlaneMesh.new()
	mesh.size = half_extent * 2.0 + Vector2.ONE * 400.0
	mesh.material = _material(Color(0.82, 0.74, 0.58))
	var mi := MeshInstance3D.new()
	mi.name = "Ground"
	mi.mesh = mesh
	parent.add_child(mi)

	var body := StaticBody3D.new()
	body.name = "GroundBody"
	var shape := CollisionShape3D.new()
	shape.shape = WorldBoundaryShape3D.new()
	body.add_child(shape)
	# Invisible walls just outside the map edge.
	var h := half_extent
	for wall in [[Vector3(0, 5, -h.y), Vector3(h.x * 2, 10, 1)], [Vector3(0, 5, h.y), Vector3(h.x * 2, 10, 1)],
			[Vector3(-h.x, 5, 0), Vector3(1, 10, h.y * 2)], [Vector3(h.x, 5, 0), Vector3(1, 10, h.y * 2)]]:
		var cs := CollisionShape3D.new()
		var box := BoxShape3D.new()
		box.size = wall[1]
		cs.shape = box
		cs.position = wall[0]
		body.add_child(cs)
	parent.add_child(body)


func _build_roads(parent: Node3D, road_list: Array) -> void:
	var major := PackedVector3Array()
	var minor := PackedVector3Array()
	for r in road_list:
		var pts := PackedVector2Array()
		for p in r.get("points", []):
			pts.append(geo.to_local2(p[0], p[1]))
		if pts.size() < 2:
			continue
		var highway := str(r.get("highway", "road"))
		roads.add_road({"name": str(r.get("name", "")), "highway": highway, "maxspeed_kmh": r.get("maxspeed_kmh")}, pts)
		var is_major := highway in MAJOR
		var width: float = ROAD_WIDTH.get(highway, 6.0)
		var lanes = r.get("lanes")
		if lanes != null and int(lanes) > 0:
			width = maxf(width, int(lanes) * 3.5 * (1.0 if r.get("oneway", false) else 2.0) * 0.5 + 3.5)
		_add_ribbon(major if is_major else minor, pts, width, 0.06 if is_major else 0.04)
	_add_mesh(parent, "MajorRoads", major, Color(0.22, 0.22, 0.24))
	_add_mesh(parent, "MinorRoads", minor, Color(0.33, 0.33, 0.35))


## Flat quads along the polyline; each segment is stretched a little so bends have no gaps.
func _add_ribbon(out: PackedVector3Array, pts: PackedVector2Array, width: float, y: float) -> void:
	var hw := width * 0.5
	for i in pts.size() - 1:
		var a := pts[i]
		var b := pts[i + 1]
		var d := b - a
		if d.length() < 0.01:
			continue
		var dir := d.normalized()
		a -= dir * hw * 0.5
		b += dir * hw * 0.5
		var n := Vector2(-dir.y, dir.x) * hw
		var a1 := Vector3(a.x + n.x, y, a.y + n.y)
		var a2 := Vector3(a.x - n.x, y, a.y - n.y)
		var b1 := Vector3(b.x + n.x, y, b.y + n.y)
		var b2 := Vector3(b.x - n.x, y, b.y - n.y)
		_add_up_tri(out, a1, b1, b2)
		_add_up_tri(out, a1, b2, a2)


## Appends a triangle wound so that it faces up (+Y).
func _add_up_tri(out: PackedVector3Array, a: Vector3, b: Vector3, c: Vector3) -> void:
	if (b - a).cross(c - a).y > 0.0:
		out.append_array([a, c, b])
	else:
		out.append_array([a, b, c])


func _add_mesh(parent: Node3D, node_name: String, verts: PackedVector3Array, color: Color) -> MeshInstance3D:
	if verts.is_empty():
		return null
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for v in verts:
		st.add_vertex(v)
	st.generate_normals()
	var mi := MeshInstance3D.new()
	mi.name = node_name
	mi.mesh = st.commit()
	mi.material_override = _material(color)
	parent.add_child(mi)
	return mi


func _building_height(bld: Dictionary) -> float:
	var h = bld.get("height")
	if h != null and float(h) > 0.0:
		return float(h)
	var levels = bld.get("levels")
	if levels != null and float(levels) > 0.0:
		return float(levels) * LEVEL_HEIGHT
	# Unknown height: 2-4 storeys, stable per building.
	return 6.0 + float(hash(str(bld.get("id", ""))) % 7)


func _build_buildings(parent: Node3D, list: Array) -> void:
	var verts := PackedVector3Array()
	var walls := PackedVector3Array()
	for bld in list:
		var poly := PackedVector2Array()
		for p in bld.get("points", []):
			poly.append(geo.to_local2(p[0], p[1]))
		if poly.size() < 3:
			continue
		var h := _building_height(bld)
		# Roofs, raised structures and anything drawn over a road (e.g. elevated metro
		# stations) are shown but not solid, so roads are never blocked.
		var min_h = bld.get("min_height")
		var solid: bool = not (str(bld.get("kind", "yes")) in NO_COLLISION_KINDS) \
				and (min_h == null or float(min_h) <= 0.0) and not roads.crosses_polygon(poly)
		var base := float(min_h) if min_h != null else 0.0
		# Positive signed area in (x, z) makes the walls face outward.
		if _signed_area(poly) < 0.0:
			poly.reverse()
		for i in poly.size():
			var a := poly[i]
			var b := poly[(i + 1) % poly.size()]
			var a0 := Vector3(a.x, base, a.y)
			var b0 := Vector3(b.x, base, b.y)
			var a1 := Vector3(a.x, h, a.y)
			var b1 := Vector3(b.x, h, b.y)
			var quad := [a0, b0, b1, a0, b1, a1]
			verts.append_array(quad)
			if solid:
				walls.append_array(quad)
		var tris := Geometry2D.triangulate_polygon(poly)
		for t in range(0, tris.size(), 3):
			var p0 := poly[tris[t]]
			var p1 := poly[tris[t + 1]]
			var p2 := poly[tris[t + 2]]
			_add_up_tri(verts, Vector3(p0.x, h, p0.y), Vector3(p1.x, h, p1.y), Vector3(p2.x, h, p2.y))
	var mi := _add_mesh(parent, "Buildings", verts, Color(0.62, 0.62, 0.64))
	if mi == null or walls.is_empty():
		return
	var shape := ConcavePolygonShape3D.new()
	shape.backface_collision = true
	shape.set_faces(walls)
	var body := StaticBody3D.new()
	body.name = "BuildingCollision"
	var cs := CollisionShape3D.new()
	cs.shape = shape
	body.add_child(cs)
	parent.add_child(body)


func _signed_area(poly: PackedVector2Array) -> float:
	var a := 0.0
	for i in poly.size():
		var p := poly[i]
		var q := poly[(i + 1) % poly.size()]
		a += p.x * q.y - q.x * p.y
	return a * 0.5


func _build_fuel(parent: Node3D, list: Array) -> void:
	var mat := _material(Color(0.85, 0.15, 0.12))
	for f in list:
		var pos := geo.to_local(f["lat"], f["lon"])
		var label := str(f.get("name", ""))
		if label.is_empty():
			label = str(f.get("brand", ""))
		if label.is_empty():
			label = "Petrol station"
		fuel_stations.append({"name": label, "position": pos})
		var mi := MeshInstance3D.new()
		mi.name = "Fuel"
		var box := BoxMesh.new()
		box.size = Vector3(6, 4, 6)
		box.material = mat
		mi.mesh = box
		mi.position = pos + Vector3(0, 2, 0)
		parent.add_child(mi)
