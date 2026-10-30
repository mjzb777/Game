extends SceneTree
## Clicks every district button in the main menu and checks that the right
## district scene loads, then returns to the menu.
## godot --headless --path . -s res://tests/test_menu.gd

var failures := 0


func _initialize() -> void:
	_run()


func _frames(n: int) -> void:
	for i in n:
		await process_frame


func _run() -> void:
	var game = root.get_node("Game")
	game.save_path = "user://test_menu_save.json"
	change_scene_to_file("res://scenes/main_menu.tscn")
	await _frames(3)
	var ids: Array = []
	for d in DistrictRegistry.list_districts():
		ids.append(d["id"])
	for i in ids.size():
		var buttons := _district_buttons()
		if buttons.size() != ids.size():
			_fail("menu shows %d district buttons, expected %d" % [buttons.size(), ids.size()])
			break
		print("  click: ", buttons[i].text)
		buttons[i].pressed.emit()
		await _frames(5)
		var scene := current_scene
		if scene == null or scene.get("district_id") != ids[i]:
			_fail("button %d did not load district %s (got %s)" % [i, ids[i], scene.name if scene else "null"])
		elif scene.car == null or scene.hud == null:
			_fail("%s loaded without car/HUD" % ids[i])
		else:
			print("  ok   %s -> %s (roads: %d segments, load_error: '%s')" % [ids[i], scene.scene_file_path, scene.roads.segment_count(), scene.load_error])
		game.back_to_menu()
		await _frames(3)
	DirAccess.remove_absolute(ProjectSettings.globalize_path(game.save_path))
	print("menu test: %d failures" % failures)
	quit(1 if failures > 0 else 0)


func _district_buttons() -> Array:
	var out: Array = []
	for b in current_scene.find_children("*", "Button", true, false):
		if "missions)" in b.text:
			out.append(b)
	return out


func _fail(msg: String) -> void:
	failures += 1
	printerr("  FAIL ", msg)
