class_name ChaseCamera
extends Camera3D
## Third-person camera that follows behind the target and looks slightly ahead.

@export var distance := 9.0
@export var height := 3.8
@export var follow_speed := 6.0

var target: Node3D


func _ready() -> void:
	top_level = true
	far = 4000.0
	snap()


func snap() -> void:
	if target:
		global_position = _desired()
		look_at(target.global_position + Vector3.UP * 1.5)


func _desired() -> Vector3:
	var back := target.global_transform.basis.z
	back.y = 0.0
	back = back.normalized() if back.length() > 0.01 else Vector3.BACK
	return target.global_position + back * distance + Vector3.UP * height


func _physics_process(delta: float) -> void:
	if target == null:
		return
	global_position = global_position.lerp(_desired(), clampf(follow_speed * delta, 0.0, 1.0))
	look_at(target.global_position + Vector3.UP * 1.5 - target.global_transform.basis.z * 4.0)
