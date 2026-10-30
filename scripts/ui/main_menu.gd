extends Control
## Main menu: lists districts found in data/districts/ and loads the chosen scene.


func _ready() -> void:
	set_anchors_preset(Control.PRESET_FULL_RECT)
	var bg := ColorRect.new()
	bg.color = Color(0.1, 0.12, 0.16)
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(bg)

	var v := VBoxContainer.new()
	v.set_anchors_and_offsets_preset(Control.PRESET_CENTER)
	v.custom_minimum_size = Vector2(520, 0)
	v.offset_left = -260
	v.offset_right = 260
	v.offset_top = -260
	v.add_theme_constant_override("separation", 10)
	add_child(v)

	var title := Label.new()
	title.text = "Dubai Drive"
	title.add_theme_font_size_override("font_size", 48)
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	v.add_child(title)

	var money := Label.new()
	money.text = "Wallet: AED %d   |   Missions completed: %d" % [Game.money, Game.completed.size()]
	money.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	v.add_child(money)

	var pick := Label.new()
	pick.text = "Choose a district"
	pick.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	v.add_child(pick)

	for d in DistrictRegistry.list_districts():
		var b := Button.new()
		b.custom_minimum_size = Vector2(0, 44)
		var ready_count := 0
		for m in d.get("missions", []):
			var ok := true
			for role in ["pickup", "dropoff", "target"]:
				if m.has(role) and not (m[role].get("resolved") is Dictionary):
					ok = false
			if ok:
				ready_count += 1
		b.text = "%s   (%d/%d missions)" % [d.get("name", d["id"]), ready_count, d.get("missions", []).size()]
		if not DistrictRegistry.has_osm_data(d):
			b.text += "   - no map data yet"
		var did: String = d["id"]
		b.pressed.connect(func() -> void: Game.play_district(did))
		v.add_child(b)

	var reset := Button.new()
	reset.text = "Reset save"
	reset.pressed.connect(func() -> void:
		Game.reset_save()
		get_tree().reload_current_scene())
	v.add_child(reset)
	var quit := Button.new()
	quit.text = "Quit"
	quit.pressed.connect(func() -> void: get_tree().quit())
	v.add_child(quit)
