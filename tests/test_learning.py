"""Learning the house: daily records, cooling time constant and heat-up rate."""
from __future__ import annotations

from datetime import datetime, timedelta
import math
from zoneinfo import ZoneInfo

import pytest

from custom_components.degree_dealer.learning import HouseLearner, RoomLearner

TZ = ZoneInfo("Europe/Copenhagen")
STEP = timedelta(minutes=5)


def test_room_learns_time_constant_from_a_night_without_heating() -> None:
    room = RoomLearner()
    tau, outdoor = 60.0, 0.0
    start = datetime(2026, 11, 2, 22, 0, tzinfo=TZ)
    for step in range(5 * 12):  # 5 hours
        now = start + step * STEP
        temp = outdoor + 21 * math.exp(-(step * STEP).total_seconds() / 3600 / tau)
        room.update(now, temp, 17.0, 0.0, outdoor, night=True)
    room.update(start + 61 * STEP, 18.0, 21.0, 100.0, outdoor, night=True)  # heating starts again
    assert room.time_constant == pytest.approx(tau, rel=0.02)
    assert room.time_constant_samples == 1


def test_open_window_cancels_the_episode() -> None:
    room = RoomLearner()
    start = datetime(2026, 11, 2, 22, 0, tzinfo=TZ)
    for step in range(4 * 12):
        temp = 21 - step * 0.02
        room.update(start + step * STEP, temp, 17.0, 0.0, 0.0, night=True, paused=step == 30)
    room.update(start + 50 * STEP, 19.0, 21.0, 100.0, 0.0, night=True)
    assert room.time_constant is None


def test_room_learns_heat_up_rate() -> None:
    room = RoomLearner()
    start = datetime(2026, 11, 3, 5, 0, tzinfo=TZ)
    for step in range(13):  # one hour from 19 to 21 °C
        room.update(start + step * STEP, 19 + step * 2 / 12, 21.0, 100.0, 0.0, night=False)
    room.update(start + 13 * STEP, 21.0, 21.0, 20.0, 0.0, night=False)
    # the episode ends when the room is within 0.3 K of the setpoint
    assert room.heat_up_rate == pytest.approx(2.0)


def test_cold_minutes() -> None:
    room = RoomLearner()
    now = datetime(2026, 11, 3, 8, 0, tzinfo=TZ)
    room.update(now, 19.0, 21.0, 100.0, 0.0, night=False)
    assert room.cold_minutes(now + timedelta(minutes=40)) == pytest.approx(40)
    room.update(now + timedelta(minutes=45), 21.0, 21.0, 30.0, 0.0, night=False)
    assert room.cold_minutes(now + timedelta(minutes=50)) == 0


def test_daily_records_give_heat_loss_samples_with_meter_lag() -> None:
    house = HouseLearner()
    start = datetime(2026, 11, 1, 0, 0, tzinfo=TZ)
    meter = 1000.0
    for day in range(5):
        for step in range(288):
            now = start + timedelta(days=day) + step * STEP
            house.add_sample(now, {"indoor": 21.0, "outdoor": 1.0 + day}, meter)
        meter += 50 + day  # energy of the day is known at the end of it
    house.add_sample(start + timedelta(days=5), {"indoor": 21.0, "outdoor": 6.0}, meter)
    assert len(house.days) == 5
    samples = house.heat_loss_samples(0)
    # day 1 (outdoor 2 °C) used 51 kWh
    assert samples[0] == (19.0, 51.0)
    # with one day lag the 51 kWh that arrived on day 1 belong to day 0 (outdoor 1 °C)
    assert house.heat_loss_samples(1)[0] == (20.0, 51.0)


def test_learner_survives_a_restart() -> None:
    house = HouseLearner()
    house.room("living").time_constant = 42.0
    house.update_comfort(21.0)
    restored = HouseLearner.from_dict(house.to_dict())
    assert restored.time_constant() == 42.0
    assert restored.comfort == 21.0
