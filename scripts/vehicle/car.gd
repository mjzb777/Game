class_name Car
extends CharacterBody3D
## Simple arcade car: throttle / brake / steer, gravity, slides along walls.
## Speed is in m/s internally; speed_kmh() is what the HUD shows.

@export var max_speed := 55.0  # m/s; with drag the real top speed is ~155 km/h
@export var max_reverse := 8.0
@export var acceleration := 12.0
@export var brake_force := 18.0
@export var handbrake_force := 25.0
@export var air_drag := 0.0012
@export var rolling := 0.4
@export var steer_rate := 1.8  # rad/s at low speed

var speed := 0.0  # signed, forward positive
var throttle := 0.0
var fuel: FuelSystem
## Set by tests to drive without the keyboard: {throttle, brake, steer, handbrake}.
var input_override: Dictionary = {}


func _ready() -> void:
	var shape := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = Vector3(1.9, 1.2, 4.4)
	shape.shape = box
	shape.position.y = 0.75
	add_child(shape)
	_add_box(Vector3(1.9, 0.8, 4.4), Vector3(0, 0.6, 0), Color(0.9, 0.9, 0.95))
	_add_box(Vector3(1.6, 0.6, 2.2), Vector3(0, 1.3, 0.3), Color(0.15, 0.2, 0.3))
	_add_box(Vector3(1.7, 0.2, 0.1), Vector3(0, 0.7, -2.21), Color(1.0, 0.9, 0.4))  # headlights = front
	fuel = FuelSystem.new()
	fuel.name = "Fuel"
	add_child(fuel)
	floor_snap_length = 0.5


func _add_box(size: Vector3, pos: Vector3, color: Color) -> void:
	var mi := MeshInstance3D.new()
	var mesh := BoxMesh.new()
	mesh.size = size
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mesh.material = mat
	mi.mesh = mesh
	mi.position = pos
	add_child(mi)


func speed_kmh() -> float:
	return absf(speed) * 3.6


func forward() -> Vector3:
	return -global_transform.basis.z


func _read_input() -> Dictionary:
	if not input_override.is_empty():
		return input_override
	return {
		"throttle": Input.get_action_strength("accelerate"),
		"brake": Input.get_action_strength("brake"),
		"steer": Input.get_action_strength("steer_left") - Input.get_action_strength("steer_right"),
		"handbrake": Input.is_action_pressed("handbrake"),
	}


func _physics_process(delta: float) -> void:
	var inp := _read_input()
	throttle = float(inp.get("throttle", 0.0))
	var brake := float(inp.get("brake", 0.0))
	var steer := float(inp.get("steer", 0.0))
	if fuel.is_empty():
		throttle = 0.0

	if throttle > 0.0:
		speed += acceleration * throttle * delta * (1.0 - clampf(speed / max_speed, 0.0, 1.0))
	if brake > 0.0:
		if speed > 0.5:
			speed -= brake_force * brake * delta
		elif not fuel.is_empty():
			speed -= acceleration * 0.6 * brake * delta  # reverse
	if inp.get("handbrake", false):
		speed = move_toward(speed, 0.0, handbrake_force * delta)
	# Drag and rolling resistance.
	speed -= signf(speed) * (air_drag * speed * speed + rolling) * delta
	if absf(speed) < 0.05 and throttle == 0.0 and brake == 0.0:
		speed = 0.0
	speed = clampf(speed, -max_reverse, max_speed)

	# Steering: needs movement, gets gentler at high speed.
	var speed_factor := clampf(absf(speed) / 6.0, 0.0, 1.0) * lerpf(1.0, 0.45, clampf(absf(speed) / max_speed, 0.0, 1.0))
	rotate_y(steer * steer_rate * speed_factor * signf(speed) * delta)

	var vy := velocity.y
	if not is_on_floor():
		vy -= 20.0 * delta
	else:
		vy = minf(vy, 0.0)
	velocity = forward() * speed
	velocity.y = vy
	var before := global_position
	move_and_slide()
	# After hitting a wall, keep only the speed that is actually along the car.
	if get_slide_collision_count() > 0:
		var horizontal := Vector3(velocity.x, 0, velocity.z)
		var along := horizontal.dot(forward())
		if absf(along) < absf(speed):
			speed = along
	var moved := Vector2(global_position.x - before.x, global_position.z - before.z).length()
	fuel.consume(moved, throttle)


## Places the car on the ground at pos, facing dir (in the X/Z plane), stopped.
func place(pos: Vector3, dir: Vector2) -> void:
	var yaw := atan2(-dir.x, -dir.y) if dir.length() > 0.01 else 0.0
	global_transform = Transform3D(Basis(Vector3.UP, yaw), Vector3(pos.x, 0.5, pos.z))
	speed = 0.0
	velocity = Vector3.ZERO
