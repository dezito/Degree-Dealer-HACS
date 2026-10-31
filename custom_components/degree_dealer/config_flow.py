"""Config and options flow: heating plant, rooms, meter data and economy."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar, selector

from .const import (
    CONF_AREAS,
    CONF_DASHBOARD,
    CONF_EXPECTED_RETURN,
    CONF_HEAT_ENERGY,
    CONF_HEAT_PRICE,
    CONF_MANUAL_UA,
    CONF_MAX_SETBACK,
    CONF_METER_LAG,
    CONF_MIN_ROOM_TEMP,
    CONF_MIN_SAVING,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_OUTDOOR,
    CONF_RECOVERY_RETURN_RISE,
    CONF_RETURN,
    CONF_RETURN_PENALTY,
    CONF_ROOMS,
    CONF_SUPPLY,
    CONF_SUPPLY_MAX,
    CONF_SUPPLY_MIN,
    CONF_SUPPLY_REFERENCE,
    CONF_TARGET_RETURN,
    CONF_WEATHER,
    DEFAULT_NAME,
    DOMAIN,
    ECONOMY_KEYS,
    METER_KEYS,
    PLANT_KEYS,
    ROOM_AREA,
    ROOM_CLIMATES,
    ROOM_ENTITY_KEYS,
    ROOM_EXCLUDE,
    ROOM_HUMIDITY,
    ROOM_MOTION,
    ROOM_OPENINGS,
    ROOM_TEMPERATURES,
    ROOM_VALVES,
    default_options,
)
from .discovery import discover_room


def _entity(domain: str | list[str], multiple: bool = False) -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain, multiple=multiple))


def _number(minimum: float, maximum: float, step: float, unit: str | None = None) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=selector.NumberSelectorMode.BOX)
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _suggested(options: dict[str, Any], key: str) -> dict[str, Any]:
    """Optional fields use a suggested value, so the user can clear them again."""
    return {"suggested_value": options.get(key)}


def _replace(options: dict[str, Any], keys: tuple[str, ...], user_input: dict[str, Any]) -> None:
    """A cleared optional field is missing from user_input and must disappear from the options."""
    for key in keys:
        options.pop(key, None)
    options.update(user_input)


class _HeatingSteps:
    """Steps shared by the config flow and the options flow."""

    _options: dict[str, Any]
    _room_queue: list[str]
    _rooms: list[dict[str, Any]]

    async def _async_finish(self) -> ConfigFlowResult:
        raise NotImplementedError

    async def async_step_plant(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_SUPPLY] == user_input[CONF_RETURN]:
                errors["base"] = "same_entity"
            elif not user_input.get(CONF_OUTDOOR) and not user_input.get(CONF_WEATHER):
                errors["base"] = "outdoor_required"
            else:
                _replace(self._options, PLANT_KEYS, user_input)
                return await self.async_step_rooms()
        options = {**self._options, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(CONF_SUPPLY, description=_suggested(options, CONF_SUPPLY)): _entity("sensor"),
                vol.Required(CONF_RETURN, description=_suggested(options, CONF_RETURN)): _entity("sensor"),
                vol.Optional(CONF_OUTDOOR, description=_suggested(options, CONF_OUTDOOR)): _entity("sensor"),
                vol.Optional(CONF_WEATHER, description=_suggested(options, CONF_WEATHER)): _entity("weather"),
                vol.Optional(CONF_SUPPLY_REFERENCE, description=_suggested(options, CONF_SUPPLY_REFERENCE)): _entity(
                    ["sensor", "number", "input_number"]
                ),
                vol.Required(CONF_SUPPLY_MIN, default=options[CONF_SUPPLY_MIN]): _number(10, 90, 1, "°C"),
                vol.Required(CONF_SUPPLY_MAX, default=options[CONF_SUPPLY_MAX]): _number(20, 130, 1, "°C"),
            }
        )
        return self.async_show_form(step_id="plant", data_schema=schema, errors=errors)  # type: ignore[attr-defined]

    async def async_step_rooms(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_AREAS):
                self._room_queue = list(user_input[CONF_AREAS])
                self._rooms = []
                return await self.async_step_room()
            errors["base"] = "no_areas"
        current = [room[ROOM_AREA] for room in self._options.get(CONF_ROOMS, [])]
        schema = vol.Schema(
            {
                vol.Required(CONF_AREAS, description={"suggested_value": current or None}): selector.AreaSelector(
                    selector.AreaSelectorConfig(multiple=True)
                )
            }
        )
        return self.async_show_form(step_id="rooms", data_schema=schema, errors=errors)  # type: ignore[attr-defined]

    async def async_step_room(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """One page per area, filled in with what was found in the area (or chosen before)."""
        area_id = self._room_queue[0]
        area = ar.async_get(self.hass).async_get_area(area_id)  # type: ignore[attr-defined]
        name = area.name if area else area_id
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(ROOM_CLIMATES) and not user_input.get(ROOM_TEMPERATURES):
                errors["base"] = "room_empty"
            else:
                room = {ROOM_AREA: area_id, ROOM_EXCLUDE: bool(user_input.get(ROOM_EXCLUDE, False))}
                room.update({key: list(user_input.get(key) or []) for key in ROOM_ENTITY_KEYS})
                self._rooms.append(room)
                self._room_queue.pop(0)
                if self._room_queue:
                    return await self.async_step_room()
                self._options[CONF_ROOMS] = self._rooms
                return await self.async_step_meter()
        previous = next((room for room in self._options.get(CONF_ROOMS, []) if room[ROOM_AREA] == area_id), None)
        values = user_input or previous or discover_room(self.hass, area_id)  # type: ignore[attr-defined]
        domains = {
            ROOM_CLIMATES: "climate",
            ROOM_TEMPERATURES: "sensor",
            ROOM_VALVES: ["sensor", "number"],
            ROOM_OPENINGS: "binary_sensor",
            ROOM_MOTION: "binary_sensor",
            ROOM_HUMIDITY: "sensor",
        }
        fields: dict[Any, Any] = {
            vol.Optional(key, description={"suggested_value": values.get(key) or None}): _entity(domain, True)
            for key, domain in domains.items()
        }
        fields[vol.Required(ROOM_EXCLUDE, default=bool(values.get(ROOM_EXCLUDE, False)))] = selector.BooleanSelector()
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="room", data_schema=vol.Schema(fields), errors=errors, description_placeholders={"room": name}
        )

    async def async_step_meter(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            user_input[CONF_METER_LAG] = int(user_input[CONF_METER_LAG])
            _replace(self._options, METER_KEYS, user_input)
            return await self.async_step_economy()
        options = self._options
        schema = vol.Schema(
            {
                vol.Optional(CONF_HEAT_ENERGY, description=_suggested(options, CONF_HEAT_ENERGY)): _entity("sensor"),
                vol.Required(CONF_METER_LAG, default=options[CONF_METER_LAG]): _number(0, 7, 1, "d"),
                vol.Optional(CONF_EXPECTED_RETURN, description=_suggested(options, CONF_EXPECTED_RETURN)): _entity(
                    ["sensor", "number", "input_number"]
                ),
                vol.Required(CONF_TARGET_RETURN, default=options[CONF_TARGET_RETURN]): _number(10, 80, 0.5, "°C"),
            }
        )
        return self.async_show_form(step_id="meter", data_schema=schema)  # type: ignore[attr-defined]

    async def async_step_economy(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            _replace(self._options, ECONOMY_KEYS, user_input)
            return await self.async_step_dashboard()
        options = self._options
        schema = vol.Schema(
            {
                vol.Required(CONF_HEAT_PRICE, default=options[CONF_HEAT_PRICE]): _number(0, 100, 0.01),
                vol.Required(CONF_RETURN_PENALTY, default=options[CONF_RETURN_PENALTY]): _number(0, 1000, 0.1),
                vol.Required(CONF_RECOVERY_RETURN_RISE, default=options[CONF_RECOVERY_RETURN_RISE]): _number(
                    0, 30, 0.5, "K"
                ),
                vol.Required(CONF_MIN_SAVING, default=options[CONF_MIN_SAVING]): _number(0, 1000, 0.1),
                vol.Required(CONF_MIN_ROOM_TEMP, default=options[CONF_MIN_ROOM_TEMP]): _number(5, 25, 0.5, "°C"),
                vol.Required(CONF_MAX_SETBACK, default=options[CONF_MAX_SETBACK]): _number(0.5, 10, 0.5, "K"),
                vol.Required(CONF_NIGHT_START, default=options[CONF_NIGHT_START]): selector.TimeSelector(),
                vol.Required(CONF_NIGHT_END, default=options[CONF_NIGHT_END]): selector.TimeSelector(),
                vol.Required(CONF_MANUAL_UA, default=options[CONF_MANUAL_UA]): _number(0, 5000, 1, "W/K"),
            }
        )
        return self.async_show_form(step_id="economy", data_schema=schema)  # type: ignore[attr-defined]

    async def async_step_dashboard(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._options[CONF_DASHBOARD] = bool(user_input[CONF_DASHBOARD])
            return await self._async_finish()
        schema = vol.Schema(
            {vol.Required(CONF_DASHBOARD, default=self._options[CONF_DASHBOARD]): selector.BooleanSelector()}
        )
        return self.async_show_form(step_id="dashboard", data_schema=schema)  # type: ignore[attr-defined]


class DegreeDealerConfigFlow(_HeatingSteps, ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._options = default_options()
        self._room_queue = []
        self._rooms = []
        self._title = DEFAULT_NAME

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DegreeDealerOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._title = user_input[CONF_NAME]
            return await self.async_step_plant()
        schema = vol.Schema({vol.Required(CONF_NAME, default=DEFAULT_NAME): str})
        return self.async_show_form(step_id="user", data_schema=schema)

    async def _async_finish(self) -> ConfigFlowResult:
        return self.async_create_entry(title=self._title, data={}, options=self._options)


class DegreeDealerOptionsFlow(_HeatingSteps, OptionsFlow):
    def __init__(self) -> None:
        self._room_queue = []
        self._rooms = []

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._options = {**default_options(), **self.config_entry.options}
        return await self.async_step_plant()

    async def _async_finish(self) -> ConfigFlowResult:
        return self.async_create_entry(data=self._options)
