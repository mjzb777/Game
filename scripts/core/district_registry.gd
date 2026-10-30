class_name DistrictRegistry
extends RefCounted
## Finds districts by reading data/districts/*.json. Adding a district = adding a file.

const DATA_DIR := "res://data/districts"
const GENERIC_SCENE := "res://scenes/district.tscn"


static func list_districts() -> Array:
	var result: Array = []
	var dir := DirAccess.open(DATA_DIR)
	if dir == null:
		push_error("Missing %s" % DATA_DIR)
		return result
	var files := Array(dir.get_files())
	files.sort()
	for f in files:
		if f.ends_with(".json"):
			var d = read_json(DATA_DIR.path_join(f))
			if d is Dictionary and d.has("id"):
				result.append(d)
	return result


static func load_district(district_id: String) -> Dictionary:
	var d = read_json(DATA_DIR.path_join(district_id + ".json"))
	return d if d is Dictionary else {}


## A district may have its own scene in scenes/districts/<id>.tscn; otherwise the
## generic district scene is used with the id from Game.current_district_id.
static func scene_path(district_id: String) -> String:
	var own := "res://scenes/districts/%s.tscn" % district_id
	return own if ResourceLoader.exists(own) else GENERIC_SCENE


static func osm_path(district: Dictionary) -> String:
	return str(district.get("osm_file", "res://data/osm/%s.json" % district.get("id", "")))


static func has_osm_data(district: Dictionary) -> bool:
	return FileAccess.file_exists(osm_path(district))


static func read_json(path: String) -> Variant:
	if not FileAccess.file_exists(path):
		return null
	var parsed = JSON.parse_string(FileAccess.get_file_as_string(path))
	if parsed == null:
		push_error("Invalid JSON: %s" % path)
	return parsed
