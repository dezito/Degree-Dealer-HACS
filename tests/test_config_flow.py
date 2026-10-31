"""Config flow, options flow and finding the entities of an area."""
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.degree_dealer.const import (
    CONF_AREAS,
    CONF_HEAT_PRICE,
    CONF_OUTDOOR,
    CONF_RETURN,
    CONF_ROOMS,
    CONF_SUPPLY,
    DOMAIN,
    ROOM_AREA,
    ROOM_CLIMATES,
    ROOM_EXCLUDE,
    ROOM_HUMIDITY,
    ROOM_MOTION,
    ROOM_OPENINGS,
    ROOM_TEMPERATURES,
    ROOM_VALVES,
    default_options,
)
from custom_components.degree_dealer.discovery import discover_room

from .conftest import OUTDOOR, RETURN, SUPPLY, set_plant


def _suggested(result, key: str):
    for field in result["data_schema"].schema:
        if field == key:
            return field.description["suggested_value"]
    raise KeyError(key)


async def test_discover_room_finds_everything_in_the_area(hass: HomeAssistant, areas: dict[str, str]) -> None:
    found = discover_room(hass, areas["living"])
    assert found[ROOM_CLIMATES] == ["climate.living_trv"]
    # the thermostat's own temperature sensor is left out, the room sensor is used
    assert found[ROOM_TEMPERATURES] == ["sensor.living_temperature"]
    assert found[ROOM_VALVES] == ["sensor.living_heating_power"]
    assert found[ROOM_OPENINGS] == ["binary_sensor.living_window"]
    assert found[ROOM_MOTION] == ["binary_sensor.living_motion"]
    assert found[ROOM_HUMIDITY] == ["sensor.living_humidity"]
    assert discover_room(hass, areas["bath"])[ROOM_CLIMATES] == ["climate.bath_trv"]


async def test_full_config_flow(hass: HomeAssistant, areas: dict[str, str]) -> None:
    set_plant(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "House"})
    assert result["step_id"] == "plant"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SUPPLY: SUPPLY, CONF_RETURN: SUPPLY, CONF_OUTDOOR: OUTDOOR, "supply_min_temperature": 20, "supply_max_temperature": 80}
    )
    assert result["errors"] == {"base": "same_entity"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SUPPLY: SUPPLY, CONF_RETURN: RETURN, "supply_min_temperature": 20, "supply_max_temperature": 80}
    )
    assert result["errors"] == {"base": "outdoor_required"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SUPPLY: SUPPLY, CONF_RETURN: RETURN, CONF_OUTDOOR: OUTDOOR, "supply_min_temperature": 20, "supply_max_temperature": 80}
    )
    assert result["step_id"] == "rooms"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_AREAS: [areas["living"], areas["bath"]]})
    assert result["step_id"] == "room"
    assert result["description_placeholders"] == {"room": "Living room"}
    assert _suggested(result, ROOM_TEMPERATURES) == ["sensor.living_temperature"]
    assert _suggested(result, ROOM_OPENINGS) == ["binary_sensor.living_window"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            ROOM_CLIMATES: ["climate.living_trv"],
            ROOM_TEMPERATURES: ["sensor.living_temperature"],
            ROOM_VALVES: ["sensor.living_heating_power"],
            ROOM_OPENINGS: ["binary_sensor.living_window"],
            ROOM_EXCLUDE: False,
        },
    )
    assert result["description_placeholders"] == {"room": "Bathroom"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {ROOM_EXCLUDE: True})
    assert result["errors"] == {"base": "room_empty"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {ROOM_CLIMATES: ["climate.bath_trv"], ROOM_EXCLUDE: True}
    )
    assert result["step_id"] == "meter"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"meter_lag_days": 1, "target_return_temperature": 35}
    )
    assert result["step_id"] == "economy"
    economy = {key: value for key, value in default_options().items() if key in result["data_schema"].schema}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**economy, CONF_HEAT_PRICE: 0.9})
    assert result["step_id"] == "dashboard"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"dashboard": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["dashboard"] is False
    assert result["title"] == "House"
    options = result["options"]
    assert options[CONF_HEAT_PRICE] == 0.9
    assert options["meter_lag_days"] == 1
    assert [room[ROOM_AREA] for room in options[CONF_ROOMS]] == [areas["living"], areas["bath"]]
    assert options[CONF_ROOMS][0][ROOM_MOTION] == []
    assert options[CONF_ROOMS][1][ROOM_EXCLUDE] is True


async def test_options_flow_keeps_the_rooms_and_clears_a_field(hass: HomeAssistant, entry, areas) -> None:
    set_plant(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "plant"
    # outdoor sensor removed, weather used instead
    hass.states.async_set("weather.home", "cloudy", {"temperature": 4.0})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SUPPLY: SUPPLY, CONF_RETURN: RETURN, "weather_entity_id": "weather.home", "supply_min_temperature": 25, "supply_max_temperature": 70},
    )
    assert result["step_id"] == "rooms"
    assert _suggested(result, CONF_AREAS) == [areas["living"], areas["bath"]]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_AREAS: [areas["living"]]})
    # the room page shows what was chosen before, not what discovery finds
    assert _suggested(result, ROOM_MOTION) == ["binary_sensor.living_motion"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {ROOM_CLIMATES: ["climate.living_trv"], ROOM_EXCLUDE: False}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"meter_lag_days": 0, "target_return_temperature": 32}
    )
    economy = {key: value for key, value in default_options().items() if key in result["data_schema"].schema}
    result = await hass.config_entries.options.async_configure(result["flow_id"], economy)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"dashboard": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert CONF_OUTDOOR not in entry.options
    assert entry.options["weather_entity_id"] == "weather.home"
    assert len(entry.options[CONF_ROOMS]) == 1
