class_name FuelSystem
extends Node
## Fuel tank: burns fuel with distance, speed and throttle (and a little at idle);
## refuels for money, only at real petrol stations (see Hud._try_refuel).

signal fuel_changed(liters: float, capacity: float)
signal ran_out

## Consumption is exaggerated about 8x so fuel matters inside a 3 km district
## (a full 40 L tank lasts roughly 30-45 km of town driving).
@export var capacity := 40.0
@export var price_per_liter := 3.0  # AED, about the 2025-26 UAE Special 95 price
## Litres per km at a steady 50 km/h with light throttle.
@export var base_per_km := 0.55
## Extra litres per km at full throttle (accelerating hard burns far more).
@export var throttle_extra_per_km := 0.9
## Aerodynamic drag: extra litres per km grows with the square of speed.
## At 100 km/h this adds 0.45 L/km, at 140 km/h 0.88 L/km.
@export var drag_per_km_at_100 := 0.45
## Engine running while stopped (litres per minute).
@export var idle_per_minute := 0.08

var liters := 40.0


func is_empty() -> bool:
	return liters <= 0.0


## Litres per km the car is burning right now (for the HUD and tests).
func rate_per_km(speed_kmh: float, throttle: float) -> float:
	var v := speed_kmh / 100.0
	return base_per_km + throttle_extra_per_km * clampf(throttle, 0.0, 1.0) + drag_per_km_at_100 * v * v


func consume(distance_m: float, throttle: float, speed_kmh: float = 50.0, delta: float = 0.0) -> void:
	if is_empty():
		return
	var used := idle_per_minute / 60.0 * delta
	if distance_m > 0.0:
		used += distance_m / 1000.0 * rate_per_km(speed_kmh, throttle)
	if used <= 0.0:
		return
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
	var affordable := float(maxi(Game.money, 0)) / price_per_liter
	var amount := minf(wanted, affordable)
	if amount < 0.1:
		return 0.0
	var cost := int(ceil(amount * price_per_liter))
	if not Game.spend_money(mini(cost, Game.money)):
		return 0.0
	add(amount)
	return amount
