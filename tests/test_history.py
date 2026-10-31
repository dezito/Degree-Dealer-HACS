"""Learning from the recorder: statistics, recorded states and the replay."""
from __future__ import annotations

from datetime import datetime, timedelta
import math

import pytest

from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.degree_dealer.history import FULL_DAY, HistoryReader, daily_records_from_statistics

from .conftest import set_climate, set_plant


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations) -> None:  # type: ignore[no-untyped-def]
    """The recorder has to start before hass, so it is requested first in this module."""


def _state(entity_id: str, value: str, when: datetime) -> State:
    return State(entity_id, value, last_changed=when, last_updated=when)


def test_history_reader_moves_forward_through_the_changes() -> None:
    start = datetime(2026, 1, 10, 22, 0, tzinfo=dt_util.UTC)
    reader = HistoryReader(
        {"sensor.t": [_state("sensor.t", "20", start + timedelta(hours=1)), _state("sensor.t", "21", start)]}
    )
    reader.advance(start - timedelta(minutes=5))
    assert reader.get("sensor.t") is None
    reader.advance(start + timedelta(minutes=30))
    assert reader.get("sensor.t").state == "21"  # type: ignore[union-attr]
    reader.advance(start + timedelta(hours=2))
    assert reader.get("sensor.t").state == "20"  # type: ignore[union-attr]
    assert reader.get("sensor.unknown") is None


def test_daily_records_from_statistics() -> None:
    day = dt_util.start_of_local_day(datetime(2025, 12, 1))
    ts = [(day + timedelta(days=i)).timestamp() for i in range(3)]
    statistics = {
        "sensor.room": [{"start": ts[0], "mean": 21.0}, {"start": ts[1], "mean": 20.0}],
        "sensor.outdoor": [{"start": ts[0], "mean": 2.0}, {"start": ts[1], "mean": 4.0}],
        "sensor.meter": [{"start": t, "state": 1000.0 + 60 * i} for i, t in enumerate(ts)],
    }
    records = daily_records_from_statistics(statistics, ["sensor.room", "sensor.missing"], "sensor.outdoor", "sensor.meter")
    assert [record["date"] for record in records] == ["2025-12-01", "2025-12-02", "2025-12-03"]
    assert records[0] == {
        "date": "2025-12-01", "meter_kwh": 1000.0, "samples": FULL_DAY, "indoor": 21.0, "outdoor": 2.0, "source": "statistics"
    }
    # the last day only has a meter reading: it is not a full day, but closes the one before
    assert records[2]["samples"] == 0 and records[2]["meter_kwh"] == 1120.0


async def test_learns_a_night_from_the_recorded_states(hass: HomeAssistant, entry, freezer) -> None:
    tau = 40.0
    night = dt_util.start_of_local_day() - timedelta(days=2) + timedelta(hours=22)
    freezer.move_to(night)
    set_plant(hass, outdoor=0)
    set_climate(hass, "climate.bath_trv", 21.0, 21.0, "idle")
    hass.states.async_set("sensor.living_heating_power", "0", {"unit_of_measurement": "%"})
    for step in range(16):  # the valve stays closed for 8 hours while the room cools
        freezer.move_to(night + timedelta(minutes=30 * step))
        temperature = round(21 * math.exp(-step * 0.5 / tau), 3)
        hass.states.async_set("sensor.living_temperature", str(temperature))
        set_climate(hass, "climate.living_trv", temperature, 17.0, "idle")
        await hass.async_block_till_done()
    freezer.move_to(night + timedelta(hours=8))
    hass.states.async_set("sensor.living_heating_power", "100", {"unit_of_measurement": "%"})
    set_climate(hass, "climate.living_trv", 18.0, 21.0, "heating")
    await async_wait_recording_done(hass)

    freezer.move_to(night + timedelta(days=1, hours=12))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    learner = entry.runtime_data.learner
    assert learner.history["status"] == "done"
    assert learner.history["state_days"] >= 2
    living = learner.rooms[entry.options["rooms"][0]["area_id"]]
    # 22:30 (after the radiator has settled) to the end of the night
    assert living.time_constant == pytest.approx(tau, rel=0.15)
    state = hass.states.get("sensor.degree_dealer_cooling_time_constant")
    assert state is not None and float(state.state) == pytest.approx(tau, rel=0.15)
