extends SceneTree
## Compiles every .gd file in the project and exits 1 on any error.
## godot --headless --path . -s res://tests/check_scripts.gd


func _initialize() -> void:
	var failed := 0
	var files: Array = []
	_collect("res://scripts", files)
	_collect("res://tests", files)
	for path in files:
		var s = load(path)
		if s == null or not (s is GDScript) or not s.can_instantiate():
			printerr("SCRIPT ERROR: ", path)
			failed += 1
	print("Checked %d scripts, %d with errors" % [files.size(), failed])
	quit(1 if failed > 0 else 0)


func _collect(dir_path: String, out: Array) -> void:
	var dir := DirAccess.open(dir_path)
	if dir == null:
		return
	for d in dir.get_directories():
		_collect(dir_path.path_join(d), out)
	for f in dir.get_files():
		if f.ends_with(".gd"):
			out.append(dir_path.path_join(f))
