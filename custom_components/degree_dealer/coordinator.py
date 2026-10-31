"""Reads the heating entities, learns the house and calculates the advice every 5 minutes.

Reading (``snapshot``) and learning (``learn``) take a state getter, so the same code learns
from the live states and from recorded history (history.py).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONTROL_MODE,
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
    CONF_RETURN_LIMIT,
    CONF_RETURN_LIMIT_MAX,
    CONF_RETURN_LIMIT_MIN,
    CONF_RETURN_PENALTY,
    CONF_ROOMS,
    CONF_SUPPLY,
    CONF_SUPPLY_MAX,
    CONF_SUPPLY_MIN,
    CONF_SUPPLY_REFERENCE,
    CONF_TARGET_RETURN,
    CONF_WEATHER,
    CONTROL_RETURN,
    CONTROL_SUPPLY,
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
    Advice,
    HeatLossFit,
    RoomDemand,
    SetbackInputs,
    SetbackOption,
    advise_return_limit,
    advise_supply,
    fit_heat_loss,
    in_window,
    next_window,
    plan_setback,
)

_LOGGER = logging.getLogger(__name__)

TO_KWH = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0, "GJ": 277.778, "MJ": 0.277778}
HVAC_ACTION_DEMAND = {"heating": 100.0, "preheating": 100.0, "idle": 0.0, "off": 0.0}

StateGetter = Callable[[str], State | None]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _number(get: StateGetter, entity_id: str | None, attribute: str | None = None) -> float | None:
    if not entity_id or (state := get(entity_id)) is None:
        return None
    value = state.attributes.get(attribute) if attribute else state.state
    if value in (None, STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _energy_kwh(get: StateGetter, entity_id: str | None) -> float | None:
    value = _number(get, entity_id)
    if value is None or (state := get(entity_id or "")) is None:
        return None
    factor = TO_KWH.get(state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, "kWh"))
    return None if factor is None else value * factor


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)


@dataclass
class RoomReading:
    """The current states of one area."""

    name: str
    temperature: float | None = None
    setpoint: float | None = None
    demand: float | None = None
    window_open: bool = False
    last_motion: datetime | None = None
    humidity: float | None = None


@dataclass
class Snapshot:
    """Everything read at one moment."""

    outdoor: float | None
    supply: float | None
    return_temperature: float | None
    meter_kwh: float | None
    rooms: list[tuple[dict[str, Any], RoomReading]]


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
    control_mode: str = CONTROL_RETURN
    # current value of what is controlled (reference/limit entity, else the measured value)
    advice_base: float | None = None
    rooms: list[RoomStatus] = field(default_factory=list)
    advice: Advice | None = None
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
    history: dict[str, Any] = field(default_factory=dict)

    @property
    def key_demand(self) -> float | None:
        return self.advice.key_demand if self.advice else None


class DegreeDealerCoordinator(DataUpdateCoordinator[DegreeDealerData]):
    """One Degree Dealer instance (one heating plant)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=UPDATE_INTERVAL, config_entry=entry)
        self.entry = entry
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.learner = HouseLearner()
        # while history is replayed into a new learner, the live samples are not learnt
        self.history_running = False

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

    async def async_learn_from_history(self) -> None:
        """Start over from everything the recorder has (runs in the background)."""
        from .history import async_learn_from_history  # recorder is only imported when used

        if self.history_running:
            return
        self.history_running = True
        self.learner.history = {"status": "running", "started_at": dt_util.now().isoformat()}
        self.async_update_listeners()
        try:
            self.learner = await async_learn_from_history(self.hass, self)
        except Exception:  # noqa: BLE001 - a failing history must not break live learning
            _LOGGER.exception("Learning from the history failed")
            self.learner.history = {"status": "failed", "learnt_at": dt_util.now().isoformat()}
        finally:
            self.history_running = False
        await self.async_save()
        await self.async_refresh()

    # -- reading ----------------------------------------------------------------------

    def _room(self, get: StateGetter, room: dict[str, Any], now: datetime) -> RoomReading:
        """Everything in one area: mean temperature, mean setpoint, highest valve demand, windows, motion."""
        area = ar.async_get(self.hass).async_get_area(room[ROOM_AREA])
        reading = RoomReading(name=area.name if area else room[ROOM_AREA])
        climates = [
            state
            for entity_id in room.get(ROOM_CLIMATES, [])
            if (state := get(entity_id)) is not None and state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
        ]
        heating = [state for state in climates if state.state != "off"]

        temperatures = [value for entity_id in room.get(ROOM_TEMPERATURES, []) if (value := _number(get, entity_id)) is not None]
        if not temperatures:
            temperatures = [
                value for state in climates if (value := _number(get, state.entity_id, "current_temperature")) is not None
            ]
        reading.temperature = _mean(temperatures)
        reading.setpoint = _mean(
            [value for state in heating if (value := _number(get, state.entity_id, "temperature")) is not None]
        )

        valves = [value for entity_id in room.get(ROOM_VALVES, []) if (value := _number(get, entity_id)) is not None]
        if valves:
            reading.demand = min(max(max(valves), 0.0), 100.0)
        elif climates and not heating:
            reading.demand = 0.0
        else:
            actions = [HVAC_ACTION_DEMAND.get(str(state.attributes.get("hvac_action"))) for state in heating]
            known = [value for value in actions if value is not None]
            reading.demand = max(known) if known else None

        reading.window_open = any(
            (state := get(entity_id)) is not None and state.state == STATE_ON for entity_id in room.get(ROOM_OPENINGS, [])
        )
        motion_states = [state for entity_id in room.get(ROOM_MOTION, []) if (state := get(entity_id))]
        if any(state.state == STATE_ON for state in motion_states):
            reading.last_motion = now
        elif motion_states:
            reading.last_motion = max(state.last_changed for state in motion_states)
        reading.humidity = _mean(
            [value for entity_id in room.get(ROOM_HUMIDITY, []) if (value := _number(get, entity_id)) is not None]
        )
        return reading

    def snapshot(self, get: StateGetter, now: datetime) -> Snapshot:
        options = self.options
        outdoor = _number(get, options.get(CONF_OUTDOOR))
        if outdoor is None:
            outdoor = _number(get, options.get(CONF_WEATHER), "temperature")
        return Snapshot(
            outdoor=outdoor,
            supply=_number(get, options.get(CONF_SUPPLY)),
            return_temperature=_number(get, options.get(CONF_RETURN)),
            meter_kwh=_energy_kwh(get, options.get(CONF_HEAT_ENERGY)),
            rooms=[(room, self._room(get, room, now)) for room in options[CONF_ROOMS]],
        )

    # -- learning ---------------------------------------------------------------------

    def learn(self, learner: HouseLearner, now: datetime, snapshot: Snapshot) -> None:
        options = self.options
        night = in_window(now, _parse_time(options[CONF_NIGHT_START]), _parse_time(options[CONF_NIGHT_END]))
        setpoints: list[float] = []
        for room, reading in snapshot.rooms:
            learner.room(room[ROOM_AREA]).update(
                now, reading.temperature, reading.setpoint, reading.demand, snapshot.outdoor, night, paused=reading.window_open
            )
            if reading.setpoint is not None:
                setpoints.append(reading.setpoint)
        if setpoints and not night:
            learner.update_comfort(sum(setpoints) / len(setpoints))
        indoor = _mean([reading.temperature for _, reading in snapshot.rooms if reading.temperature is not None])
        learner.add_sample(
            now,
            {"indoor": indoor, "outdoor": snapshot.outdoor, "supply": snapshot.supply, "return": snapshot.return_temperature},
            snapshot.meter_kwh,
        )

    # -- calculation ------------------------------------------------------------------

    def calculate(self, now: datetime) -> DegreeDealerData:
        options = self.options
        learner = self.learner
        snapshot = self.snapshot(self.hass.states.get, now)
        if not self.history_running:
            self.learn(learner, now, snapshot)

        data = DegreeDealerData(
            outdoor=snapshot.outdoor,
            supply=snapshot.supply,
            return_temperature=snapshot.return_temperature,
            learning_days=len(learner.days),
            history=dict(learner.history or {}),
        )
        temperatures: list[float] = []
        setpoints: list[float] = []
        for room, reading in snapshot.rooms:
            area_id = room[ROOM_AREA]
            room_learner = learner.room(area_id)
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

        # return temperature target
        expected = _number(self.hass.states.get, options.get(CONF_EXPECTED_RETURN))
        if expected is not None:
            data.return_target, data.return_target_source = expected, "expected_return"
        else:
            data.return_target, data.return_target_source = float(options[CONF_TARGET_RETURN]), "configured"

        # advice for what the controller can control, from the valves
        data.control_mode = options[CONF_CONTROL_MODE]
        demands = [RoomDemand(room.name, room.demand, room.cold_minutes, room.exclude) for room in data.rooms]
        if data.control_mode == CONTROL_SUPPLY:
            reference = _number(self.hass.states.get, options.get(CONF_SUPPLY_REFERENCE))
            data.advice_base = reference if reference is not None else data.supply
            data.advice = advise_supply(
                data.advice_base, demands, float(options[CONF_SUPPLY_MIN]), float(options[CONF_SUPPLY_MAX])
            )
        elif data.control_mode == CONTROL_RETURN:
            limit = _number(self.hass.states.get, options.get(CONF_RETURN_LIMIT))
            data.advice_base = limit if limit is not None else data.return_temperature
            data.advice = advise_return_limit(
                data.advice_base,
                data.return_temperature,
                demands,
                data.return_target,
                float(options[CONF_RETURN_LIMIT_MIN]),
                float(options[CONF_RETURN_LIMIT_MAX]),
            )

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
        data.comfort = learner.comfort if learner.comfort is not None else _mean(setpoints)

        # night setback economics
        data.window = next_window(now, _parse_time(options[CONF_NIGHT_START]), _parse_time(options[CONF_NIGHT_END]))
        data.setback_missing = [
            key
            for key, value in (
                ("heat_loss_coefficient", data.ua),
                ("time_constant", data.time_constant),
                ("comfort_temperature", data.comfort),
                ("outdoor_temperature", data.outdoor),
            )
            if value is None
        ]
        if not data.setback_missing:
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
