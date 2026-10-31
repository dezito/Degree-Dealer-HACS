"""Learn from Home Assistant's recorder, as far back as it has data.

1. Long-term statistics (kept forever) give daily means of the room temperature sensors and
   the outdoor sensor and the daily meter reading, for the days before the recorded states.
   They feed the heat loss fit. Climate attributes and sensors without a state class have
   no statistics.
2. Recorded states (the recorder's keep_days) are replayed in 5 minute steps through the
   same snapshot/learn code as the live updates, so the time constant, heat-up rate,
   comfort temperature and the recent days are learnt exactly as they would have been live.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from functools import partial
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance, history
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HEAT_ENERGY,
    CONF_OUTDOOR,
    CONF_RETURN,
    CONF_ROOMS,
    CONF_SUPPLY,
    CONF_WEATHER,
    ROOM_ENTITY_KEYS,
    ROOM_TEMPERATURES,
)
from .learning import MAX_DAYS, HouseLearner

if TYPE_CHECKING:
    from .coordinator import DegreeDealerCoordinator

_LOGGER = logging.getLogger(__name__)

STEP = timedelta(minutes=5)
CHUNK = timedelta(days=1)
FULL_DAY = 288  # samples of a day from the statistics count as a full day
STATISTICS_START = datetime(2000, 1, 1, tzinfo=dt_util.UTC)
UNITS = {"energy": "kWh", "temperature": "°C"}


class HistoryReader:
    """The state of each entity at a moment, from recorded state changes (moving forward only)."""

    def __init__(self, states: dict[str, list[State]]) -> None:
        self._states = {entity_id: sorted(items, key=lambda state: state.last_updated) for entity_id, items in states.items()}
        self._index = dict.fromkeys(self._states, -1)

    def advance(self, moment: datetime) -> None:
        for entity_id, items in self._states.items():
            index = self._index[entity_id]
            while index + 1 < len(items) and items[index + 1].last_updated <= moment:
                index += 1
            self._index[entity_id] = index

    def get(self, entity_id: str) -> State | None:
        index = self._index.get(entity_id, -1)
        return self._states[entity_id][index] if index >= 0 else None


def daily_records_from_statistics(
    statistics: dict[str, list[dict[str, Any]]],
    temperature_ids: list[str],
    outdoor_id: str | None,
    meter_id: str | None,
) -> list[dict[str, Any]]:
    """Daily records in the same format as HouseLearner.days."""

    def by_day(statistic_id: str | None) -> dict[date, dict[str, Any]]:
        rows = statistics.get(statistic_id or "", [])
        return {dt_util.as_local(dt_util.utc_from_timestamp(row["start"])).date(): row for row in rows}

    rooms = [by_day(statistic_id) for statistic_id in temperature_ids]
    outdoor = by_day(outdoor_id)
    meter = by_day(meter_id)
    records = []
    for day in sorted(set(outdoor) | set(meter)):
        indoor_values = [room[day]["mean"] for room in rooms if day in room and room[day].get("mean") is not None]
        indoor = sum(indoor_values) / len(indoor_values) if indoor_values else None
        outdoor_mean = outdoor[day].get("mean") if day in outdoor else None
        meter_kwh = meter[day].get("state") if day in meter else None
        full = indoor is not None and outdoor_mean is not None
        records.append(
            {
                "date": day.isoformat(),
                "meter_kwh": meter_kwh,
                "samples": FULL_DAY if full else 0,
                "indoor": None if indoor is None else round(indoor, 3),
                "outdoor": None if outdoor_mean is None else round(outdoor_mean, 3),
                "source": "statistics",
            }
        )
    return records


def _entity_ids(options: dict[str, Any]) -> list[str]:
    ids = [options.get(key) for key in (CONF_SUPPLY, CONF_RETURN, CONF_OUTDOOR, CONF_WEATHER, CONF_HEAT_ENERGY)]
    for room in options.get(CONF_ROOMS, []):
        for key in ROOM_ENTITY_KEYS:
            ids.extend(room.get(key) or [])
    return sorted({entity_id for entity_id in ids if entity_id})


async def async_learn_from_history(hass: HomeAssistant, coordinator: DegreeDealerCoordinator) -> HouseLearner:
    """A new learner built from everything the recorder has."""
    learner = HouseLearner()
    started = dt_util.now()
    if "recorder" not in hass.config.components:
        learner.history = {"status": "no_recorder", "learnt_at": started.isoformat()}
        return learner
    instance = get_instance(hass)
    if not await instance.async_db_ready:
        learner.history = {"status": "no_recorder", "learnt_at": started.isoformat()}
        return learner
    options = coordinator.options
    states_start = dt_util.start_of_local_day(started - timedelta(days=instance.keep_days))

    # 1. statistics for the days before the recorded states
    temperature_ids = sorted({entity_id for room in options.get(CONF_ROOMS, []) for entity_id in room.get(ROOM_TEMPERATURES) or []})
    statistic_ids = {*temperature_ids, *(options.get(key) for key in (CONF_OUTDOOR, CONF_HEAT_ENERGY) if options.get(key))}
    statistics_days = 0
    if statistic_ids:
        statistics = await instance.async_add_executor_job(
            statistics_during_period, hass, STATISTICS_START, states_start, statistic_ids, "day", UNITS, {"mean", "state"}
        )
        records = daily_records_from_statistics(statistics, temperature_ids, options.get(CONF_OUTDOOR), options.get(CONF_HEAT_ENERGY))
        learner.days = records[-MAX_DAYS:]
        statistics_days = sum(1 for record in learner.days if record["samples"])

    # 2. replay the recorded states
    entity_ids = _entity_ids(options)
    state_days = 0
    first_day: date | None = date.fromisoformat(learner.days[0]["date"]) if learner.days else None
    moment = states_start
    while moment < started and entity_ids:
        chunk_end = min(moment + CHUNK, started)
        states = await instance.async_add_executor_job(
            partial(
                history.get_significant_states,
                hass,
                moment,
                chunk_end,
                entity_ids,
                include_start_time_state=True,
                significant_changes_only=False,
            )
        )
        if any(states.values()):
            reader = HistoryReader(states)  # type: ignore[arg-type]
            step = moment
            while step < chunk_end:
                reader.advance(step)
                coordinator.learn(learner, step, coordinator.snapshot(reader.get, step))
                step += STEP
            state_days += 1
            first_day = first_day or moment.date()
        moment = chunk_end
        await asyncio.sleep(0)  # let Home Assistant work between the days

    learner.history = {
        "status": "done",
        "learnt_at": dt_util.now().isoformat(),
        "first_day": first_day.isoformat() if first_day else None,
        "statistics_days": statistics_days,
        "state_days": state_days,
    }
    _LOGGER.info(
        "Learnt from the history: %s days of statistics and %s days of recorded states (from %s)",
        statistics_days,
        state_days,
        first_day,
    )
    return learner
