class_name DistrictBuilder
extends RefCounted
## Builds a district from OSM JSON (tools/fetch_osm.py output):
## ground plane, flat road ribbons (bridges drawn above, tunnels below), extruded
## gray buildings, petrol stations, traffic signals and roundabout islands.

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
const FUEL_STATION_RADIUS := 20.0
const EDGE_MARGIN := 150.0
## Ribbon heights. The world is flat, so a bridge and the road under it overlap in plan;
## stacking them by OSM layer keeps the bridge visible on top without z-fighting.
const ROAD_Y := {"tunnel": 0.02, "minor": 0.04, "major": 0.06, "bridge": 0.10}
const LAYER_Y := 0.02

var geo: Geo
var roads := RoadNetwork.new()
var fuel_stations: Array = []  # [{name, brand, position: Vector3 (station), access: Vector3 (on road), on_network}]
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
	_build_roundabouts(parent, osm.get("roundabouts", []))
	_build_signals(parent, osm.get("signals", []))


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
	var bridges := PackedVector3Array()
	var tunnels := PackedVector3Array()
	for r in road_list:
		var pts := PackedVector2Array()
		for p in r.get("points", []):
			pts.append(geo.to_local2(p[0], p[1]))
		if pts.size() < 2:
			continue
		var highway := str(r.get("highway", "road"))
		var is_bridge: bool = r.get("bridge", false)
		var is_tunnel: bool = r.get("tunnel", false)
		roads.add_road({
			"name": str(r.get("name", "")), "highway": highway, "maxspeed_kmh": r.get("maxspeed_kmh"),
			"oneway": r.get("oneway", false), "connected": r.get("connected", true),
			"private": r.get("private", false), "bridge": is_bridge, "tunnel": is_tunnel,
			"layer": int(r.get("layer", 0)),
		}, pts)
		var is_major := highway in MAJOR
		var width: float = ROAD_WIDTH.get(highway, 6.0)
		var lanes = r.get("lanes")
		if lanes != null and int(lanes) > 0:
			width = maxf(width, int(lanes) * 3.5 * (1.0 if r.get("oneway", false) else 2.0) * 0.5 + 3.5)
		var layer := int(r.get("layer", 0))
		if is_bridge:
			_add_ribbon(bridges, pts, width, ROAD_Y["bridge"] + maxi(layer - 1, 0) * LAYER_Y)
		elif is_tunnel:
			_add_ribbon(tunnels, pts, width, ROAD_Y["tunnel"])
		else:
			_add_ribbon(major if is_major else minor, pts, width, ROAD_Y["major"] if is_major else ROAD_Y["minor"])
	_add_mesh(parent, "MajorRoads", major, Color(0.22, 0.22, 0.24))
	_add_mesh(parent, "MinorRoads", minor, Color(0.33, 0.33, 0.35))
	_add_mesh(parent, "Bridges", bridges, Color(0.46, 0.45, 0.43))
	_add_mesh(parent, "Tunnels", tunnels, Color(0.16, 0.14, 0.12))


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
	# Old data files without a height: 2-4 storeys, stable per building.
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
		# stations, podiums over an underpass) are shown but not solid, so roads are never blocked.
		var min_h = bld.get("min_height")
		var base := float(min_h) if min_h != null else 0.0
		if base >= h - 0.5:
			base = 0.0  # inverted min_height/height in the data
		var solid: bool = bld.get("solid", true) and not (str(bld.get("kind", "yes")) in NO_COLLISION_KINDS) \
				and base <= 0.0 and not roads.crosses_polygon(poly)
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
		if tris.is_empty():
			# Footprints the triangulator rejects (touching edges, spikes): roof over the hull.
			poly = Geometry2D.convex_hull(poly)
			poly.remove_at(poly.size() - 1)
			tris = Geometry2D.triangulate_polygon(poly)
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
	var pump_mat := _material(Color(0.95, 0.8, 0.1))
	for f in list:
		var pos := geo.to_local(f["lat"], f["lon"])
		# The OSM point can sit inside the station shop; the car refuels at the forecourt
		# point on the nearest road that tools/fetch_osm.py worked out.
		var access := pos
		var on_network := true
		var a = f.get("access")
		if a is Dictionary:
			access = geo.to_local(a["lat"], a["lon"])
			on_network = a.get("on_main_network", true)
		var label := str(f.get("name", ""))
		var brand := str(f.get("brand", ""))
		if label.is_empty():
			label = brand
		if label.is_empty():
			label = "Petrol station"
		elif not brand.is_empty() and not label.contains(brand):
			label = "%s %s" % [brand, label]
		fuel_stations.append({"name": label, "brand": brand, "position": pos, "access": access, "on_network": on_network})
		var mi := MeshInstance3D.new()
		mi.name = "Fuel"
		var box := BoxMesh.new()
		box.size = Vector3(6, 4, 6)
		box.material = mat
		mi.mesh = box
		mi.position = pos + Vector3(0, 2, 0)
		parent.add_child(mi)
		var pump := MeshInstance3D.new()
		pump.name = "FuelPump"
		var pm := BoxMesh.new()
		pm.size = Vector3(1.2, 2.0, 1.2)
		pm.material = pump_mat
		pump.mesh = pm
		pump.position = access + Vector3(0, 1.0, 0)
		parent.add_child(pump)


## Green central islands, drawn only (the island is not solid: the road ring around
## it already defines where to drive, and a solid island would trap a reset car).
func _build_roundabouts(parent: Node3D, list: Array) -> void:
	var mat := _material(Color(0.36, 0.55, 0.3))
	for r in list:
		var radius := float(r.get("radius_m", 0.0)) - 7.0
		if radius < 3.0:
			continue
		var mesh := CylinderMesh.new()
		mesh.top_radius = radius
		mesh.bottom_radius = radius
		mesh.height = 0.3
		mesh.radial_segments = 24
		mesh.material = mat
		var mi := MeshInstance3D.new()
		mi.name = "Roundabout"
		mi.mesh = mesh
		mi.position = geo.to_local(r["lat"], r["lon"], 0.15)
		parent.add_child(mi)


## Traffic signal poles from OSM highway=traffic_signals nodes (one MultiMesh).
func _build_signals(parent: Node3D, list: Array) -> void:
	var poles: Array = []
	for s in list:
		if str(s.get("kind", "")) == "traffic_signals":
			poles.append(geo.to_local(s["lat"], s["lon"]))
	if poles.is_empty():
		return
	var mesh := BoxMesh.new()
	mesh.size = Vector3(0.3, 5.0, 0.3)
	mesh.material = _material(Color(0.12, 0.12, 0.12))
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.mesh = mesh
	mm.instance_count = poles.size()
	for i in poles.size():
		mm.set_instance_transform(i, Transform3D(Basis(), poles[i] + Vector3(0, 2.5, 0)))
	var mmi := MultiMeshInstance3D.new()
	mmi.name = "TrafficSignals"
	mmi.multimesh = mm
	parent.add_child(mmi)
