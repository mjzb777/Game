class_name FuelSystem
extends Node
## Fuel tank: burns fuel per kilometre driven (more under throttle), refuels for money.

signal fuel_changed(liters: float, capacity: float)
signal ran_out

## Consumption is exaggerated so fuel matters inside a 3 km district (~25 km per tank).
@export var capacity := 40.0
@export var liters_per_km := 0.8
@export var throttle_extra_per_km := 0.8
@export var price_per_liter := 3.0  # AED

var liters := 40.0


func is_empty() -> bool:
	return liters <= 0.0


func consume(distance_m: float, throttle: float) -> void:
	if distance_m <= 0.0 or is_empty():
		return
	var used := distance_m / 1000.0 * (liters_per_km + throttle_extra_per_km * clampf(throttle, 0.0, 1.0))
	liters = maxf(liters - used, 0.0)
	fuel_changed.emit(liters, capacity)
	if is_empty():
		ran_out.emit()


func add(amount: float) -> void:
	liters = clampf(liters + amount, 0.0, capacity)
	fuel_changed.emit(liters, capacity)


## Buys as much fuel as the player can afford, up to a full tank. Returns liters bought.
func refuel_with_money() -> float:
	var wanted := capacity - liters
	var affordable := float(Game.money) / price_per_liter
	var amount := minf(wanted, affordable)
	if amount < 0.1:
		return 0.0
	var cost := int(ceil(amount * price_per_liter))
	if not Game.spend_money(mini(cost, Game.money)):
		return 0.0
	add(amount)
	return amount
