"""Setting up the entry and the sensors it publishes."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import set_climate, set_plant


def _state(hass: HomeAssistant, entity_id: str):
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


def _supply_mode(hass: HomeAssistant, entry) -> None:
    hass.config_entries.async_update_entry(entry, options={**entry.options, "control_mode": "supply"})


async def test_sensors_from_the_rooms(hass: HomeAssistant, entry) -> None:
    _supply_mode(hass, entry)
    set_plant(hass, supply=45, ret=33, outdoor=5)
    set_climate(hass, "climate.living_trv", 20.4, 21.0, "heating")
    hass.states.async_set("sensor.living_temperature", "20.0")
    hass.states.async_set("sensor.living_heating_power", "20", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.living_humidity", "48")
    hass.states.async_set("binary_sensor.living_window", "off")
    hass.states.async_set("binary_sensor.living_motion", "on")
    # the bathroom is fully open but excluded
    set_climate(hass, "climate.bath_trv", 22.0, 24.0, "heating")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    indoor = _state(hass, "sensor.degree_dealer_indoor_temperature")
    # room sensor (20.0) instead of the thermostat (20.4), mean with the bathroom
    assert float(indoor.state) == 21.0
    living = indoor.attributes["rooms"][0]
    assert living["name"] == "Living room"
    assert living["occupied"] is True
    assert living["humidity"] == 48

    advice = _state(hass, "sensor.degree_dealer_recommended_supply_temperature")
    assert float(advice.state) == 43.0
    assert advice.attributes["action"] == "lower"
    assert advice.attributes["key_room"] == "Living room"
    assert hass.states.get("sensor.degree_dealer_recommended_return_limit") is None
    # no recorder in this test: it learns from now on
    assert _state(hass, "sensor.degree_dealer_days_learnt").attributes["history_status"] == "no_recorder"

    assert float(_state(hass, "sensor.degree_dealer_cooling_supply_return").state) == 12.0
    assert float(_state(hass, "sensor.degree_dealer_return_above_target").state) == -2.0
    # nothing learnt yet: the setback is unknown and says why
    setback = _state(hass, "sensor.degree_dealer_night_setback")
    assert setback.state == "unknown"
    assert "heat_loss_coefficient" in setback.attributes["missing"]


async def test_return_limit_advice_for_direct_district_heating(hass: HomeAssistant, entry) -> None:
    """The default control mode: only the return can be limited."""
    hass.config_entries.async_update_entry(entry, options={**entry.options, "return_limit_entity_id": "number.ecl_return_limit"})
    set_plant(hass, supply=70, ret=38, outdoor=5)
    hass.states.async_set("number.ecl_return_limit", "40")
    set_climate(hass, "climate.living_trv", 21.0, 21.0, "idle")
    hass.states.async_set("sensor.living_heating_power", "40", {"unit_of_measurement": "%"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.degree_dealer_recommended_supply_temperature") is None
    advice = _state(hass, "sensor.degree_dealer_recommended_return_limit")
    # the limit (40) is above the target (35): lower it so the water gives off more heat
    assert float(advice.state) == 39.0
    assert (advice.attributes["action"], advice.attributes["reason"], advice.attributes["current"]) == ("lower", "above_target", 40.0)


async def test_learn_from_history_button(hass: HomeAssistant, entry) -> None:
    set_plant(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await hass.services.async_call("button", "press", {"entity_id": "button.degree_dealer_learn_from_history"}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.learner.history["status"] == "no_recorder"
    assert entry.runtime_data.history_running is False


async def test_open_window_keeps_the_room_out_of_the_supply_advice(hass: HomeAssistant, entry) -> None:
    _supply_mode(hass, entry)
    set_plant(hass)
    set_climate(hass, "climate.living_trv", 19.0, 21.0, "heating")
    set_climate(hass, "climate.bath_trv", 22.0, 24.0, "heating")
    hass.states.async_set("sensor.living_heating_power", "100", {"unit_of_measurement": "%"})
    hass.states.async_set("binary_sensor.living_window", "on")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    advice = _state(hass, "sensor.degree_dealer_recommended_supply_temperature")
    # every room is excluded (bathroom by choice, living room by the open window)
    assert advice.state == "unknown"
    rooms = _state(hass, "sensor.degree_dealer_indoor_temperature").attributes["rooms"]
    assert rooms[0]["window_open"] is True


async def test_manual_heat_loss_gives_a_setback_plan(hass: HomeAssistant, entry) -> None:
    coordinator_options = {**entry.options, "manual_heat_loss_coefficient": 200.0}
    hass.config_entries.async_update_entry(entry, options=coordinator_options)
    set_plant(hass, outdoor=0)
    set_climate(hass, "climate.living_trv", 21.0, 21.0, "idle")
    set_climate(hass, "climate.bath_trv", 21.0, 21.0, "idle")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    for room in coordinator.learner.rooms.values():
        room.time_constant = 30.0
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    setback = _state(hass, "sensor.degree_dealer_night_setback")
    assert float(setback.state) > 0
    assert setback.attributes["worth_it"] is True
    assert setback.attributes["best_saving_kwh"] > 0
    assert _state(hass, "sensor.degree_dealer_heat_loss_coefficient").attributes["source"] == "manual"


async def test_unload_saves_what_was_learnt(hass: HomeAssistant, entry, hass_storage) -> None:
    set_plant(hass)
    set_climate(hass, "climate.living_trv", 21.0, 21.0, "idle")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    stored = hass_storage[f"degree_dealer.{entry.entry_id}"]["data"]
    assert stored["day"] is not None
