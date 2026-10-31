"""Constants for Degree Dealer."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.const import Platform

DOMAIN = "degree_dealer"
MANUFACTURER = "Degree Dealer"
DEFAULT_NAME = "Degree Dealer"
PLATFORMS: list[Platform] = [Platform.SENSOR]

UPDATE_INTERVAL = timedelta(minutes=5)
STORAGE_VERSION = 1
# seconds between saving what has been learnt (also saved on unload)
SAVE_DELAY = 300

# Heating plant
CONF_SUPPLY = "supply_temperature_entity_id"
CONF_RETURN = "return_temperature_entity_id"
CONF_OUTDOOR = "outdoor_temperature_entity_id"
CONF_WEATHER = "weather_entity_id"
CONF_SUPPLY_REFERENCE = "supply_reference_entity_id"
CONF_SUPPLY_MIN = "supply_min_temperature"
CONF_SUPPLY_MAX = "supply_max_temperature"

# Rooms: one per Home Assistant area
CONF_AREAS = "areas"
CONF_ROOMS = "rooms"
ROOM_AREA = "area_id"
ROOM_CLIMATES = "climate_entity_ids"
ROOM_TEMPERATURES = "temperature_entity_ids"
ROOM_VALVES = "valve_entity_ids"
ROOM_OPENINGS = "opening_entity_ids"
ROOM_MOTION = "motion_entity_ids"
ROOM_HUMIDITY = "humidity_entity_ids"
ROOM_EXCLUDE = "exclude_from_supply"
ROOM_ENTITY_KEYS = (ROOM_CLIMATES, ROOM_TEMPERATURES, ROOM_VALVES, ROOM_OPENINGS, ROOM_MOTION, ROOM_HUMIDITY)
# a room counts as occupied this long after the last motion
OCCUPIED_MINUTES = 30

# Meter data
CONF_HEAT_ENERGY = "heat_energy_entity_id"
CONF_METER_LAG = "meter_lag_days"
CONF_EXPECTED_RETURN = "expected_return_entity_id"
CONF_TARGET_RETURN = "target_return_temperature"

# Economy and setback
CONF_HEAT_PRICE = "heat_price"
CONF_RETURN_PENALTY = "return_penalty"
CONF_RECOVERY_RETURN_RISE = "recovery_return_rise"
CONF_MIN_SAVING = "min_saving"
CONF_MIN_ROOM_TEMP = "min_room_temperature"
CONF_MAX_SETBACK = "max_setback"
CONF_NIGHT_START = "night_start"
CONF_NIGHT_END = "night_end"
CONF_MANUAL_UA = "manual_heat_loss_coefficient"

PLANT_KEYS = (CONF_SUPPLY, CONF_RETURN, CONF_OUTDOOR, CONF_WEATHER, CONF_SUPPLY_REFERENCE, CONF_SUPPLY_MIN, CONF_SUPPLY_MAX)
METER_KEYS = (CONF_HEAT_ENERGY, CONF_METER_LAG, CONF_EXPECTED_RETURN, CONF_TARGET_RETURN)
ECONOMY_KEYS = (
    CONF_HEAT_PRICE,
    CONF_RETURN_PENALTY,
    CONF_RECOVERY_RETURN_RISE,
    CONF_MIN_SAVING,
    CONF_MIN_ROOM_TEMP,
    CONF_MAX_SETBACK,
    CONF_NIGHT_START,
    CONF_NIGHT_END,
    CONF_MANUAL_UA,
)

# Sidebar dashboard
CONF_DASHBOARD = "dashboard"

# used until the heat-up rate of the rooms has been learnt
DEFAULT_HEAT_UP_RATE = 1.0  # K/h


def default_options() -> dict[str, Any]:
    """Options with a default value (entity ids have none)."""
    return {
        CONF_SUPPLY_MIN: 20.0,
        CONF_SUPPLY_MAX: 80.0,
        CONF_ROOMS: [],
        CONF_METER_LAG: 0,
        CONF_TARGET_RETURN: 35.0,
        CONF_HEAT_PRICE: 0.75,
        CONF_RETURN_PENALTY: 0.0,
        CONF_RECOVERY_RETURN_RISE: 3.0,
        CONF_MIN_SAVING: 1.0,
        CONF_MIN_ROOM_TEMP: 16.0,
        CONF_MAX_SETBACK: 4.0,
        CONF_NIGHT_START: "22:00:00",
        CONF_NIGHT_END: "06:00:00",
        CONF_MANUAL_UA: 0.0,
        CONF_DASHBOARD: True,
    }
