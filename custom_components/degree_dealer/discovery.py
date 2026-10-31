"""Find the entities in a Home Assistant area that Degree Dealer can use for a room."""
from __future__ import annotations

from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import ROOM_CLIMATES, ROOM_HUMIDITY, ROOM_MOTION, ROOM_OPENINGS, ROOM_TEMPERATURES, ROOM_VALVES

# words in the entity id or name of a valve opening / heating demand sensor (in %)
VALVE_WORDS = ("valve", "heating_power", "heating power", "heating_demand", "heating demand", "heat_demand", "pi_heating", "ventil")
OPENING_CLASSES = {"window", "door", "opening", "garage_door"}
MOTION_CLASSES = {"motion", "occupancy", "presence"}


def _area_of(entry: er.RegistryEntry, devices: dr.DeviceRegistry) -> str | None:
    if entry.area_id:
        return entry.area_id
    if entry.device_id and (device := devices.async_get(entry.device_id)):
        return device.area_id
    return None


def discover_room(hass: HomeAssistant, area_id: str) -> dict[str, list[str]]:
    """Thermostats, temperature, valve, window/door, motion and humidity entities of an area."""
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    found: dict[str, list[str]] = {key: [] for key in (ROOM_CLIMATES, ROOM_TEMPERATURES, ROOM_VALVES, ROOM_OPENINGS, ROOM_MOTION, ROOM_HUMIDITY)}
    in_area = [
        entry
        for entry in entities.entities.values()
        if not entry.disabled_by and entry.entity_category != EntityCategory.CONFIG and _area_of(entry, devices) == area_id
    ]
    climate_devices = {entry.device_id for entry in in_area if entry.domain == "climate" and entry.device_id}
    for entry in sorted(in_area, key=lambda item: item.entity_id):
        state = hass.states.get(entry.entity_id)
        attributes = state.attributes if state else {}
        device_class = entry.device_class or entry.original_device_class or attributes.get(ATTR_DEVICE_CLASS)
        unit = entry.unit_of_measurement or attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        text = f"{entry.entity_id} {entry.original_name or ''} {entry.name or ''}".lower()
        if entry.domain == "climate":
            found[ROOM_CLIMATES].append(entry.entity_id)
        elif entry.domain == "sensor" and device_class == "temperature":
            # a thermostat's own temperature is already its current_temperature
            if entry.device_id not in climate_devices:
                found[ROOM_TEMPERATURES].append(entry.entity_id)
        elif entry.domain == "sensor" and device_class == "humidity":
            found[ROOM_HUMIDITY].append(entry.entity_id)
        elif entry.domain == "sensor" and unit == "%" and any(word in text for word in VALVE_WORDS):
            found[ROOM_VALVES].append(entry.entity_id)
        elif entry.domain == "binary_sensor" and device_class in OPENING_CLASSES:
            found[ROOM_OPENINGS].append(entry.entity_id)
        elif entry.domain == "binary_sensor" and device_class in MOTION_CLASSES:
            found[ROOM_MOTION].append(entry.entity_id)
    return found
