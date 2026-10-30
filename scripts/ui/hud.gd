class_name Hud
extends CanvasLayer
## In-game UI: speedometer, fuel, money, road info, objective, GPS arrow, timer,
## NPC dialogue, reward popup, mission list and pause menu. Built in code.

var car: Car
var engine: MissionEngine
var roads: RoadNetwork
var district_name := ""

var _speed_label: Label
var _money_label: Label
var _fuel_bar: ProgressBar
var _fuel_label: Label
var _road_label: Label
var _objective_label: Label
var _timer_label: Label
var _distance_label: Label
var _arrow: GpsArrow
var _hint_label: Label
var _toast_label: Label
var _toast_time := 0.0
var _dialogue_panel: PanelContainer
var _dialogue_name: Label
var _dialogue_text: Label
var _dialogue_lines: Array = []
var _dialogue_index := 0
var _reward_panel: PanelContainer
var _reward_label: Label
var _reward_time := 0.0
var _mission_panel: PanelContainer
var _mission_box: VBoxContainer
var _pause_panel: PanelContainer
var _road_query_time := 0.0


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	var root := Control.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(root)

	# Top-left: district, money, fuel
	var tl := _vbox(root, Control.PRESET_TOP_LEFT, Vector2(16, 12))
	_label(tl, district_name, 20)
	_money_label = _label(tl, "", 22)
	_fuel_label = _label(tl, "", 16)
	_fuel_bar = ProgressBar.new()
	_fuel_bar.custom_minimum_size = Vector2(200, 14)
	_fuel_bar.show_percentage = false
	_fuel_bar.max_value = 100
	var fill := StyleBoxFlat.new()
	fill.bg_color = Color(0.3, 0.8, 0.3)
	_fuel_bar.add_theme_stylebox_override("fill", fill)
	tl.add_child(_fuel_bar)
	_label(tl, "[M] missions  [F] refuel  [R] reset car  [Esc] pause", 13)

	# Top-centre: objective, arrow, distance, timer
	var tc := _vbox(root, Control.PRESET_CENTER_TOP, Vector2(-300, 12))
	tc.custom_minimum_size = Vector2(600, 0)
	tc.alignment = BoxContainer.ALIGNMENT_BEGIN
	_objective_label = _label(tc, "", 20)
	_objective_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_objective_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	var row := HBoxContainer.new()
	row.alignment = BoxContainer.ALIGNMENT_CENTER
	tc.add_child(row)
	_arrow = GpsArrow.new()
	_arrow.custom_minimum_size = Vector2(56, 56)
	row.add_child(_arrow)
	_distance_label = _label(row, "", 22)
	_timer_label = _label(tc, "", 26)
	_timer_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER

	# Bottom-right: speedometer
	_speed_label = _label(root, "0 km/h", 44)
	_speed_label.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_RIGHT)
	_speed_label.offset_left = -260
	_speed_label.offset_top = -80
	_speed_label.offset_right = -20
	_speed_label.offset_bottom = -16
	_speed_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT

	# Bottom-left: road name and speed limit
	_road_label = _label(root, "", 18)
	_road_label.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_LEFT)
	_road_label.offset_left = 16
	_road_label.offset_top = -50
	_road_label.offset_right = 600
	_road_label.offset_bottom = -16

	# Centre-bottom: hints and toasts
	_hint_label = _label(root, "", 18)
	_hint_label.set_anchors_and_offsets_preset(Control.PRESET_CENTER_BOTTOM)
	_hint_label.offset_left = -300
	_hint_label.offset_right = 300
	_hint_label.offset_top = -120
	_hint_label.offset_bottom = -90
	_hint_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_toast_label = _label(root, "", 24)
	_toast_label.set_anchors_and_offsets_preset(Control.PRESET_CENTER)
	_toast_label.offset_left = -400
	_toast_label.offset_right = 400
	_toast_label.offset_top = -160
	_toast_label.offset_bottom = -120
	_toast_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER

	_build_dialogue(root)
	_build_reward(root)
	_build_mission_list(root)
	_build_pause(root)

	engine.dialogue_requested.connect(_on_dialogue)
	engine.objective_changed.connect(func(t: String) -> void: _objective_label.text = t)
	engine.mission_completed.connect(_on_completed)
	engine.mission_failed.connect(_on_failed)
	Game.money_changed.connect(func(_m: int) -> void: _update_money())
	_update_money()


func _vbox(parent: Control, preset: int, offset: Vector2) -> VBoxContainer:
	var v := VBoxContainer.new()
	v.set_anchors_and_offsets_preset(preset)
	v.position += offset
	v.mouse_filter = Control.MOUSE_FILTER_IGNORE
	parent.add_child(v)
	return v


func _label(parent: Control, text: String, font_size: int) -> Label:
	var l := Label.new()
	l.text = text
	l.add_theme_font_size_override("font_size", font_size)
	l.add_theme_color_override("font_outline_color", Color.BLACK)
	l.add_theme_constant_override("outline_size", 5)
	l.mouse_filter = Control.MOUSE_FILTER_IGNORE
	parent.add_child(l)
	return l


func _panel(parent: Control, preset: int, size: Vector2) -> PanelContainer:
	var p := PanelContainer.new()
	p.set_anchors_and_offsets_preset(preset)
	p.custom_minimum_size = size
	p.offset_left = -size.x * 0.5
	p.offset_right = size.x * 0.5
	p.visible = false
	parent.add_child(p)
	return p


func _build_dialogue(root: Control) -> void:
	_dialogue_panel = _panel(root, Control.PRESET_CENTER_BOTTOM, Vector2(700, 150))
	_dialogue_panel.offset_top = -330
	_dialogue_panel.offset_bottom = -180
	var v := VBoxContainer.new()
	_dialogue_panel.add_child(v)
	_dialogue_name = _label(v, "", 20)
	_dialogue_name.add_theme_color_override("font_color", Color(1.0, 0.85, 0.3))
	_dialogue_text = _label(v, "", 20)
	_dialogue_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_dialogue_text.custom_minimum_size = Vector2(660, 60)
	var b := Button.new()
	b.text = "Continue [Enter]"
	b.pressed.connect(_advance_dialogue)
	v.add_child(b)


func _build_reward(root: Control) -> void:
	_reward_panel = _panel(root, Control.PRESET_CENTER, Vector2(420, 110))
	_reward_panel.offset_top = -55
	_reward_panel.offset_bottom = 55
	_reward_label = _label(_reward_panel, "", 26)
	_reward_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_reward_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER


func _build_mission_list(root: Control) -> void:
	_mission_panel = _panel(root, Control.PRESET_CENTER, Vector2(760, 560))
	_mission_panel.offset_top = -280
	_mission_panel.offset_bottom = 280
	var v := VBoxContainer.new()
	_mission_panel.add_child(v)
	_label(v, "Missions - %s" % district_name, 24)
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(740, 450)
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	v.add_child(scroll)
	_mission_box = VBoxContainer.new()
	_mission_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(_mission_box)
	var h := HBoxContainer.new()
	v.add_child(h)
	var cancel := Button.new()
	cancel.text = "Cancel current mission"
	cancel.pressed.connect(func() -> void:
		engine.cancel_mission()
		_toggle_missions(false))
	h.add_child(cancel)
	var close := Button.new()
	close.text = "Close [M]"
	close.pressed.connect(func() -> void: _toggle_missions(false))
	h.add_child(close)


func _build_pause(root: Control) -> void:
	_pause_panel = _panel(root, Control.PRESET_CENTER, Vector2(300, 140))
	_pause_panel.offset_top = -70
	_pause_panel.offset_bottom = 70
	var v := VBoxContainer.new()
	_pause_panel.add_child(v)
	_label(v, "Paused", 24)
	var resume := Button.new()
	resume.text = "Resume"
	resume.pressed.connect(func() -> void: _set_paused(false))
	v.add_child(resume)
	var menu := Button.new()
	menu.text = "Main menu"
	menu.pressed.connect(func() -> void: Game.back_to_menu())
	v.add_child(menu)


func _refresh_mission_list() -> void:
	for c in _mission_box.get_children():
		c.queue_free()
	for m in engine.missions:
		var done := Game.times_completed(m["id"]) > 0
		var b := Button.new()
		b.alignment = HORIZONTAL_ALIGNMENT_LEFT
		b.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		b.custom_minimum_size = Vector2(720, 0)
		var head := "%s%s  [%s]  AED %d" % ["[done] " if done else "", m.get("title", m["id"]), m.get("type", ""), int(m.get("reward", 0))]
		if m["available"]:
			b.text = head + "\n" + engine.format_text(m, str(m.get("description", "")))
		else:
			b.text = head + "\nUnavailable: " + str(m["unavailable_reason"])
			b.disabled = true
		var mid: String = m["id"]
		b.pressed.connect(func() -> void:
			if engine.start_mission(mid):
				_toggle_missions(false)
			else:
				_toast("Finish or cancel the current mission first"))
		_mission_box.add_child(b)


func _toggle_missions(open: bool) -> void:
	_mission_panel.visible = open
	if open:
		_refresh_mission_list()


func _set_paused(p: bool) -> void:
	_pause_panel.visible = p
	get_tree().paused = p


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("pause"):
		if _mission_panel.visible:
			_toggle_missions(false)
		else:
			_set_paused(not get_tree().paused)
	elif get_tree().paused:
		return
	elif event.is_action_pressed("advance_dialogue") and _dialogue_panel.visible:
		_advance_dialogue()
	elif event.is_action_pressed("mission_list"):
		_toggle_missions(not _mission_panel.visible)
	elif event.is_action_pressed("refuel"):
		_try_refuel()
	elif event.is_action_pressed("reset_car"):
		_reset_car()
	else:
		return
	get_viewport().set_input_as_handled()


func _on_dialogue(npc: String, lines: Array) -> void:
	_dialogue_lines = lines
	_dialogue_index = 0
	if lines.is_empty():
		engine.dialogue_finished()
		return
	_dialogue_name.text = npc
	_dialogue_text.text = str(lines[0])
	_dialogue_panel.visible = true


func _advance_dialogue() -> void:
	_dialogue_index += 1
	if _dialogue_index < _dialogue_lines.size():
		_dialogue_text.text = str(_dialogue_lines[_dialogue_index])
		return
	_dialogue_panel.visible = false
	engine.dialogue_finished()


func _on_completed(m: Dictionary, reward: int) -> void:
	_reward_label.text = "Mission complete!\n%s   +AED %d" % [m.get("title", ""), reward]
	_reward_panel.visible = true
	_reward_time = 4.0


func _on_failed(m: Dictionary, reason: String) -> void:
	_dialogue_panel.visible = false
	_toast("Mission failed: %s - %s" % [m.get("title", ""), reason])


func _toast(text: String) -> void:
	_toast_label.text = text
	_toast_time = 4.0


func nearest_fuel_station() -> Dictionary:
	var parent := get_parent()
	if parent == null or not ("fuel_stations" in parent):
		return {}
	for f in parent.fuel_stations:
		var p: Vector3 = f["position"]
		if Vector2(p.x - car.global_position.x, p.z - car.global_position.z).length() < DistrictBuilder.FUEL_STATION_RADIUS:
			return f
	return {}


func _try_refuel() -> void:
	var station := nearest_fuel_station()
	if station.is_empty():
		_toast("No petrol station here")
		return
	if car.speed_kmh() > 3.0:
		_toast("Stop the car to refuel")
		return
	var bought := car.fuel.refuel_with_money()
	if bought > 0.0:
		_toast("Refuelled %.1f L at %s" % [bought, station["name"]])
	elif car.fuel.liters >= car.fuel.capacity - 0.1:
		_toast("Tank is already full")
	else:
		_toast("Not enough money")


func _reset_car() -> void:
	var p := car.global_position
	var hit := roads.nearest(Vector2(p.x, p.z), 2000.0)
	if hit.is_empty():
		car.place(p, Vector2(0, -1))
	else:
		var q: Vector2 = hit["point"]
		car.place(Vector3(q.x, 0, q.y), hit["dir"])
	# Never leave the player stranded: a free emergency can of fuel.
	if car.fuel.liters < 5.0:
		car.fuel.add(5.0 - car.fuel.liters)
		_toast("Emergency fuel: 5 L")


func _update_money() -> void:
	_money_label.text = "AED %d" % Game.money


func _process(delta: float) -> void:
	if car == null:
		return
	_speed_label.text = "%d km/h" % int(round(car.speed_kmh()))
	_fuel_bar.value = car.fuel.liters / car.fuel.capacity * 100.0
	_fuel_label.text = "Fuel %.1f / %d L" % [car.fuel.liters, int(car.fuel.capacity)]

	_road_query_time -= delta
	if _road_query_time <= 0.0:
		_road_query_time = 0.25
		var hit := roads.nearest(Vector2(car.global_position.x, car.global_position.z), 25.0)
		if hit.is_empty():
			_road_label.text = "Off road"
		else:
			var road_name: String = hit["name"] if hit["name"] != "" else "(unnamed %s road)" % hit["highway"]
			var limit = hit["maxspeed_kmh"]
			_road_label.text = road_name + ("   limit %d km/h" % int(limit) if limit != null else "")

	var target = engine.objective_position()
	_arrow.visible = target != null
	_distance_label.visible = target != null
	if target != null:
		var local: Vector3 = car.global_transform.basis.inverse() * (target - car.global_position)
		_arrow.angle = atan2(local.x, -local.z)
		_arrow.queue_redraw()
		var d := Vector2(target.x - car.global_position.x, target.z - car.global_position.z).length()
		_distance_label.text = "%d m" % int(d) if d < 1000.0 else "%.1f km" % (d / 1000.0)
	if engine.time_left >= 0.0 and engine.state == MissionEngine.State.ACTIVE:
		_timer_label.text = "%d:%02d" % [floori(engine.time_left / 60.0), int(engine.time_left) % 60]
		_timer_label.add_theme_color_override("font_color", Color.RED if engine.time_left < 10.0 else Color.WHITE)
	else:
		_timer_label.text = ""
	if engine.speeding_time > 0.0:
		_toast("SLOW DOWN! Limit %d km/h" % int(engine.current.get("speed_limit_kmh", 0)))

	var hint := ""
	if car.fuel.is_empty():
		hint = "Out of fuel! Press R for emergency fuel"
	elif not nearest_fuel_station().is_empty():
		hint = "Petrol station: stop and press F to refuel (AED %.0f/L)" % car.fuel.price_per_liter
	_hint_label.text = hint

	if _toast_time > 0.0:
		_toast_time -= delta
		if _toast_time <= 0.0:
			_toast_label.text = ""
	if _reward_time > 0.0:
		_reward_time -= delta
		if _reward_time <= 0.0:
			_reward_panel.visible = false
