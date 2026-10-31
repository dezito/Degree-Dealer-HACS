"""Reads the heating entities, learns the house and calculates the advice every 5 minutes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
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
    DEFAULT_HEAT_UP_RATE,
    DOMAIN,
    OCCUPIED_MINUTES,
    ROOM_AREA,
    ROOM_CLIMATES,
    ROOM_EXCLUDE,
    ROOM_HUMIDITY,
    ROOM_MOTION,
    ROOM_OPENINGS,
    ROOM_TEMPERATURES,
    ROOM_VALVES,
    SAVE_DELAY,
    STORAGE_VERSION,
    UPDATE_INTERVAL,
    default_options,
)
from .learning import HouseLearner
from .model import (
    HeatLossFit,
    RoomDemand,
    SetbackInputs,
    SetbackOption,
    SupplyAdvice,
    advise_supply,
    fit_heat_loss,
    in_window,
    next_window,
    plan_setback,
)

_LOGGER = logging.getLogger(__name__)

TO_KWH = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0, "GJ": 277.778, "MJ": 0.277778}
HVAC_ACTION_DEMAND = {"heating": 100.0, "preheating": 100.0, "idle": 0.0, "off": 0.0}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


@dataclass
class RoomReading:
    """The current states of one area."""

    name: str
    temperature: float | None = None
    setpoint: float | None = None
    demand: float | None = None
    heating_on: bool = False
    window_open: bool = False
    last_motion: datetime | None = None
    humidity: float | None = None


@dataclass
class RoomStatus:
    area_id: str
    name: str
    temperature: float | None
    setpoint: float | None
    demand: float | None
    cold_minutes: float
    exclude: bool
    time_constant: float | None
    heat_up_rate: float | None
    window_open: bool = False
    occupied: bool | None = None
    last_motion: datetime | None = None
    humidity: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "area_id": self.area_id,
            "name": self.name,
            "temperature": _round(self.temperature, 2),
            "setpoint": _round(self.setpoint),
            "demand": _round(self.demand),
            "cold_minutes": round(self.cold_minutes),
            "exclude_from_supply": self.exclude,
            "window_open": self.window_open,
            "occupied": self.occupied,
            "last_motion": self.last_motion.isoformat() if self.last_motion else None,
            "humidity": _round(self.humidity),
            "time_constant": _round(self.time_constant),
            "heat_up_rate": _round(self.heat_up_rate, 2),
        }


@dataclass
class DegreeDealerData:
    indoor: float | None = None
    outdoor: float | None = None
    supply: float | None = None
    return_temperature: float | None = None
    supply_base: float | None = None
    rooms: list[RoomStatus] = field(default_factory=list)
    advice: SupplyAdvice | None = None
    return_target: float | None = None
    return_target_source: str | None = None
    heat_loss: HeatLossFit | None = None
    ua: float | None = None
    ua_source: str | None = None
    time_constant: float | None = None
    heat_up_rate: float | None = None
    heat_up_rate_learnt: bool = False
    comfort: float | None = None
    window: tuple[datetime, datetime] | None = None
    setback: SetbackOption | None = None
    setback_options: list[SetbackOption] = field(default_factory=list)
    setback_missing: list[str] = field(default_factory=list)
    learning_days: int = 0

    @property
    def key_demand(self) -> float | None:
        return self.advice.key_demand if self.advice else None


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)


class DegreeDealerCoordinator(DataUpdateCoordinator[DegreeDealerData]):
    """One Degree Dealer instance (one heating plant)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=UPDATE_INTERVAL, config_entry=entry)
        self.entry = entry
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.learner = HouseLearner()

    @property
    def options(self) -> dict[str, Any]:
        return {**default_options(), **self.entry.data, **self.entry.options}

    async def async_load(self) -> None:
        if data := await self._store.async_load():
            self.learner = HouseLearner.from_dict(data)

    async def async_save(self) -> None:
        await self._store.async_save(self.learner.to_dict())

    async def async_remove_store(self) -> None:
        await self._store.async_remove()

    async def _async_update_data(self) -> DegreeDealerData:
        data = self.calculate(dt_util.now())
        self._store.async_delay_save(self.learner.to_dict, SAVE_DELAY)
        return data

    # -- reading states ---------------------------------------------------------------

    def _number(self, entity_id: str | None, attribute: str | None = None) -> float | None:
        if not entity_id or (state := self.hass.states.get(entity_id)) is None:
            return None
        value = state.attributes.get(attribute) if attribute else state.state
        if value in (None, STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _energy_kwh(self, entity_id: str | None) -> float | None:
        value = self._number(entity_id)
        if value is None or (state := self.hass.states.get(entity_id or "")) is None:
            return None
        factor = TO_KWH.get(state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, "kWh"))
        return None if factor is None else value * factor

    def _outdoor(self, options: dict[str, Any]) -> float | None:
        if (value := self._number(options.get(CONF_OUTDOOR))) is not None:
            return value
        return self._number(options.get(CONF_WEATHER), "temperature")

    def _room(self, room: dict[str, Any], now: datetime) -> RoomReading:
        """Everything in one area: mean temperature, mean setpoint, highest valve demand, windows, motion."""
        area = ar.async_get(self.hass).async_get_area(room[ROOM_AREA])
        reading = RoomReading(name=area.name if area else room[ROOM_AREA])
        climates = [
            state
            for entity_id in room.get(ROOM_CLIMATES, [])
            if (state := self.hass.states.get(entity_id)) is not None and state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
        ]
        heating = [state for state in climates if state.state != "off"]
        reading.heating_on = bool(heating)

        temperatures = [value for entity_id in room.get(ROOM_TEMPERATURES, []) if (value := self._number(entity_id)) is not None]
        if not temperatures:
            temperatures = [
                value for state in climates if (value := self._number(state.entity_id, "current_temperature")) is not None
            ]
        reading.temperature = _mean(temperatures)
        reading.setpoint = _mean(
            [value for state in heating if (value := self._number(state.entity_id, "temperature")) is not None]
        )

        valves = [value for entity_id in room.get(ROOM_VALVES, []) if (value := self._number(entity_id)) is not None]
        if valves:
            reading.demand = min(max(max(valves), 0.0), 100.0)
        elif climates and not heating:
            reading.demand = 0.0
        else:
            actions = [HVAC_ACTION_DEMAND.get(str(state.attributes.get("hvac_action"))) for state in heating]
            known = [value for value in actions if value is not None]
            reading.demand = max(known) if known else None

        reading.window_open = any(
            (state := self.hass.states.get(entity_id)) is not None and state.state == STATE_ON
            for entity_id in room.get(ROOM_OPENINGS, [])
        )
        motion_states = [state for entity_id in room.get(ROOM_MOTION, []) if (state := self.hass.states.get(entity_id))]
        if any(state.state == STATE_ON for state in motion_states):
            reading.last_motion = now
        elif motion_states:
            reading.last_motion = max(state.last_changed for state in motion_states)
        reading.humidity = _mean(
            [value for entity_id in room.get(ROOM_HUMIDITY, []) if (value := self._number(entity_id)) is not None]
        )
        return reading

    # -- calculation ------------------------------------------------------------------

    def calculate(self, now: datetime) -> DegreeDealerData:
        options = self.options
        learner = self.learner
        night_start = _parse_time(options[CONF_NIGHT_START])
        night_end = _parse_time(options[CONF_NIGHT_END])
        night = in_window(now, night_start, night_end)

        data = DegreeDealerData(
            outdoor=self._outdoor(options),
            supply=self._number(options.get(CONF_SUPPLY)),
            return_temperature=self._number(options.get(CONF_RETURN)),
        )

        temperatures: list[float] = []
        setpoints: list[float] = []
        for room in options[CONF_ROOMS]:
            area_id = room[ROOM_AREA]
            reading = self._room(room, now)
            room_learner = learner.room(area_id)
            room_learner.update(
                now, reading.temperature, reading.setpoint, reading.demand, data.outdoor, night, paused=reading.window_open
            )
            if reading.temperature is not None:
                temperatures.append(reading.temperature)
            if reading.setpoint is not None:
                setpoints.append(reading.setpoint)
            occupied = reading.last_motion is not None and (now - reading.last_motion).total_seconds() < OCCUPIED_MINUTES * 60
            data.rooms.append(
                RoomStatus(
                    area_id=area_id,
                    name=reading.name,
                    temperature=reading.temperature,
                    setpoint=reading.setpoint,
                    demand=room_learner.demand,
                    cold_minutes=room_learner.cold_minutes(now),
                    # an open window makes the valve open fully: it must not raise the supply
                    exclude=bool(room.get(ROOM_EXCLUDE)) or reading.window_open,
                    time_constant=room_learner.time_constant,
                    heat_up_rate=room_learner.heat_up_rate,
                    window_open=reading.window_open,
                    occupied=occupied if room.get(ROOM_MOTION) else None,
                    last_motion=reading.last_motion,
                    humidity=reading.humidity,
                )
            )
        data.indoor = _round(_mean(temperatures), 2)
        if setpoints and not night:
            learner.update_comfort(sum(setpoints) / len(setpoints))

        learner.add_sample(
            now,
            {"indoor": data.indoor, "outdoor": data.outdoor, "supply": data.supply, "return": data.return_temperature},
            self._energy_kwh(options.get(CONF_HEAT_ENERGY)),
        )
        data.learning_days = len(learner.days)

        # supply temperature advice from the valves
        reference = self._number(options.get(CONF_SUPPLY_REFERENCE))
        data.supply_base = reference if reference is not None else data.supply
        data.advice = advise_supply(
            data.supply_base,
            [RoomDemand(room.name, room.demand, room.cold_minutes, room.exclude) for room in data.rooms],
            float(options[CONF_SUPPLY_MIN]),
            float(options[CONF_SUPPLY_MAX]),
        )

        # return temperature target
        expected = self._number(options.get(CONF_EXPECTED_RETURN))
        if expected is not None:
            data.return_target, data.return_target_source = expected, "expected_return"
        else:
            data.return_target, data.return_target_source = float(options[CONF_TARGET_RETURN]), "configured"

        # house model
        data.heat_loss = fit_heat_loss(learner.heat_loss_samples(int(options[CONF_METER_LAG])))
        manual_ua = float(options[CONF_MANUAL_UA])
        if manual_ua > 0:
            data.ua, data.ua_source = manual_ua, "manual"
        elif data.heat_loss:
            data.ua, data.ua_source = data.heat_loss.ua, "learnt"
        data.time_constant = learner.time_constant()
        learnt_rate = learner.heat_up_rate()
        data.heat_up_rate_learnt = learnt_rate is not None
        data.heat_up_rate = learnt_rate if learnt_rate is not None else DEFAULT_HEAT_UP_RATE
        data.comfort = learner.comfort if learner.comfort is not None else (
            sum(setpoints) / len(setpoints) if setpoints else None
        )

        # night setback economics
        data.window = next_window(now, night_start, night_end)
        missing = [
            key
            for key, value in (
                ("heat_loss_coefficient", data.ua),
                ("time_constant", data.time_constant),
                ("comfort_temperature", data.comfort),
                ("outdoor_temperature", data.outdoor),
            )
            if value is None
        ]
        data.setback_missing = missing
        if not missing:
            assert data.ua is not None and data.time_constant is not None
            assert data.comfort is not None and data.outdoor is not None
            hours = (data.window[1] - data.window[0]).total_seconds() / 3600
            data.setback, data.setback_options = plan_setback(
                SetbackInputs(
                    comfort=data.comfort,
                    outdoor=data.outdoor,
                    hours=hours,
                    ua=data.ua,
                    time_constant=data.time_constant,
                    heat_up_rate=data.heat_up_rate,
                    heat_price=float(options[CONF_HEAT_PRICE]),
                    return_penalty=float(options[CONF_RETURN_PENALTY]),
                    recovery_return_rise=float(options[CONF_RECOVERY_RETURN_RISE]),
                    min_room_temperature=float(options[CONF_MIN_ROOM_TEMP]),
                ),
                float(options[CONF_MAX_SETBACK]),
                float(options[CONF_MIN_SAVING]),
            )
        return data
