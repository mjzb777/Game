class_name GpsArrow
extends Control
## Arrow that points toward the objective relative to the car's heading.

var angle := 0.0  # radians, 0 = straight ahead, positive = to the right


func _draw() -> void:
	var c := size * 0.5
	var r := minf(size.x, size.y) * 0.45
	var pts := PackedVector2Array([Vector2(0, -r), Vector2(r * 0.6, r * 0.7), Vector2(0, r * 0.35), Vector2(-r * 0.6, r * 0.7)])
	var xf := Transform2D(angle, c)
	draw_colored_polygon(xf * pts, Color(1.0, 0.85, 0.1))
	draw_polyline(xf * (pts + PackedVector2Array([pts[0]])), Color.BLACK, 2.0)
