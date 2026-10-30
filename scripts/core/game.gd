extends Node
## Global game state (autoload "Game"): money, completed missions, save data,
## input actions and scene switching.

signal money_changed(amount: int)

const MENU_SCENE := "res://scenes/main_menu.tscn"
const STARTING_MONEY := 200

var save_path := "user://save.json"
var money: int = 0
var completed: Dictionary = {}  # mission id -> number of completions
var tickets: Array = []  # [{road, limit, speed, fine}] speeding tickets, newest last
var current_district_id: String = ""


func _ready() -> void:
	_setup_input()
	load_save()


func add_money(amount: int) -> void:
	money += amount
	money_changed.emit(money)
	save()


func spend_money(amount: int) -> bool:
	if amount > money:
		return false
	money -= amount
	money_changed.emit(money)
	save()
	return true


## Fines are always charged, even into a negative balance (unpaid fines), like
## the real ones. Refuelling needs a positive balance.
func pay_fine(amount: int, record: Dictionary = {}) -> void:
	money -= amount
	if not record.is_empty():
		tickets.append(record)
		if tickets.size() > 50:
			tickets = tickets.slice(tickets.size() - 50)
	money_changed.emit(money)
	save()


func mark_completed(mission_id: String) -> void:
	completed[mission_id] = times_completed(mission_id) + 1
	save()


func times_completed(mission_id: String) -> int:
	return int(completed.get(mission_id, 0))


func save() -> void:
	var f := FileAccess.open(save_path, FileAccess.WRITE)
	if f == null:
		push_error("Cannot write save file %s" % save_path)
		return
	f.store_string(JSON.stringify({"money": money, "completed": completed, "tickets": tickets}, "  "))


func load_save() -> void:
	money = STARTING_MONEY
	completed = {}
	tickets = []
	if not FileAccess.file_exists(save_path):
		return
	var data = JSON.parse_string(FileAccess.get_file_as_string(save_path))
	if data is Dictionary:
		money = int(data.get("money", 0))
		var c = data.get("completed", {})
		if c is Dictionary:
			completed = c
		var t = data.get("tickets", [])
		if t is Array:
			tickets = t
	money_changed.emit(money)


func reset_save() -> void:
	money = STARTING_MONEY
	completed = {}
	tickets = []
	save()
	money_changed.emit(money)


func play_district(district_id: String) -> void:
	current_district_id = district_id
	get_tree().change_scene_to_file(DistrictRegistry.scene_path(district_id))


func back_to_menu() -> void:
	get_tree().paused = false
	get_tree().change_scene_to_file(MENU_SCENE)


func _setup_input() -> void:
	_add_action("accelerate", [KEY_W, KEY_UP])
	_add_action("brake", [KEY_S, KEY_DOWN])
	_add_action("steer_left", [KEY_A, KEY_LEFT])
	_add_action("steer_right", [KEY_D, KEY_RIGHT])
	_add_action("handbrake", [KEY_SPACE])
	_add_action("reset_car", [KEY_R])
	_add_action("refuel", [KEY_F])
	_add_action("mission_list", [KEY_M, KEY_TAB])
	_add_action("advance_dialogue", [KEY_ENTER, KEY_KP_ENTER, KEY_E])
	_add_action("pause", [KEY_ESCAPE])


func _add_action(action: String, keys: Array) -> void:
	if InputMap.has_action(action):
		return
	InputMap.add_action(action)
	for k in keys:
		var ev := InputEventKey.new()
		ev.physical_keycode = k
		InputMap.action_add_event(action, ev)
