"""The pure heating model."""
from __future__ import annotations

from datetime import datetime, time
import math
from zoneinfo import ZoneInfo

import pytest

from custom_components.degree_dealer.model import (
    RoomDemand,
    SetbackInputs,
    advise_return_limit,
    advise_supply,
    cooling_time_constant,
    fit_heat_loss,
    in_window,
    next_window,
    plan_setback,
    simulate_setback,
)

TZ = ZoneInfo("Europe/Copenhagen")


def test_fit_heat_loss_finds_ua_and_base_load() -> None:
    # UA 200 W/K = 4.8 kWh per K per day, 6 kWh hot water per day
    samples = [(dt, 4.8 * dt + 6) for dt in (8, 10, 12, 14, 16, 18, 20)]
    fit = fit_heat_loss(samples)
    assert fit is not None
    assert fit.ua == pytest.approx(200, rel=1e-6)
    assert fit.base_kwh_per_day == pytest.approx(6, rel=1e-6)
    assert fit.r2 == pytest.approx(1.0)


def test_fit_heat_loss_needs_days_and_spread() -> None:
    assert fit_heat_loss([(10, 50)] * 6) is None
    assert fit_heat_loss([(10 + i * 0.1, 50) for i in range(10)]) is None


def test_fit_heat_loss_never_negative_base_load() -> None:
    fit = fit_heat_loss([(dt, 5 * dt - 10) for dt in (8, 10, 12, 14, 16, 18, 20)])
    assert fit is not None
    assert fit.base_kwh_per_day == 0.0


def test_cooling_time_constant_matches_newton_cooling() -> None:
    tau = 50.0
    end = 0 + (21 - 0) * math.exp(-4 / tau)
    assert cooling_time_constant(21, end, 0, 4) == pytest.approx(tau)
    assert cooling_time_constant(21, 21.2, 0, 4) is None


def test_supply_advice_lowers_when_valves_are_mostly_closed() -> None:
    advice = advise_supply(50, [RoomDemand("Living", 20), RoomDemand("Office", 25)], 20, 80)
    assert advice is not None
    assert (advice.action, advice.target, advice.reason, advice.key_room) == ("lower", 48, "valves_mostly_closed", "Office")


def test_supply_advice_raises_for_a_starved_room_and_ignores_excluded() -> None:
    rooms = [RoomDemand("Living", 100, cold_minutes=45), RoomDemand("Bath", 100, cold_minutes=90, exclude=True)]
    advice = advise_supply(40, rooms, 20, 80)
    assert advice is not None
    assert (advice.action, advice.delta, advice.reason) == ("raise", 1.0, "rooms_starved")


def test_supply_advice_holds_in_band_and_at_limit() -> None:
    assert advise_supply(45, [RoomDemand("Living", 75)], 20, 80).action == "hold"  # type: ignore[union-attr]
    at_limit = advise_supply(20, [RoomDemand("Living", 10)], 20, 80)
    assert at_limit is not None
    assert (at_limit.action, at_limit.reason) == ("hold", "at_limit")
    assert advise_supply(None, [RoomDemand("Living", 10)], 20, 80) is None


def test_return_limit_raised_only_when_the_limiter_starves_a_room() -> None:
    starved = [RoomDemand("Living", 100, cold_minutes=45)]
    # return at the limit: the limiter holds the flow back
    advice = advise_return_limit(35, 34.5, starved, 30, 20, 60)
    assert advice is not None and (advice.action, advice.target, advice.reason) == ("raise", 36, "return_starved")
    # return well below the limit: a higher limit would not help
    advice = advise_return_limit(35, 28, starved, 30, 20, 60)
    assert advice is not None and (advice.action, advice.reason) == ("hold", "starved_not_limited")


def test_return_limit_lowered_towards_the_target() -> None:
    rooms = [RoomDemand("Living", 60)]
    advice = advise_return_limit(40, 38, rooms, 35, 20, 60)
    assert advice is not None and (advice.action, advice.target, advice.reason) == ("lower", 39, "above_target")
    advice = advise_return_limit(35, 33, rooms, 35, 20, 60)
    assert advice is not None and (advice.action, advice.reason) == ("hold", "at_target")
    advice = advise_return_limit(20, 19, rooms, 15, 20, 60)
    assert advice is not None and (advice.action, advice.reason) == ("hold", "at_limit")


def _inputs(**overrides: float) -> SetbackInputs:
    values = {
        "comfort": 21.0,
        "outdoor": 0.0,
        "hours": 8.0,
        "ua": 200.0,
        "time_constant": 40.0,
        "heat_up_rate": 1.0,
        "heat_price": 0.75,
        "return_penalty": 0.0,
        "recovery_return_rise": 3.0,
        "min_room_temperature": 16.0,
    }
    values.update(overrides)
    return SetbackInputs(**values)


def test_setback_saves_and_is_back_at_comfort_in_time() -> None:
    option = simulate_setback(_inputs(), 2.0)
    assert option.lowest_temperature >= 19.0
    assert option.saving_kwh > 0
    assert option.recovery_hours > 0
    assert option.penalty_cost == 0.0


def test_heavy_house_barely_saves() -> None:
    light = simulate_setback(_inputs(time_constant=20), 3.0)
    heavy = simulate_setback(_inputs(time_constant=300), 3.0)
    assert heavy.saving_kwh < light.saving_kwh / 3


def test_return_tariff_makes_setback_not_worth_it() -> None:
    best, options = plan_setback(_inputs(), max_setback=4, min_saving=0.5)
    assert best is not None and best.net >= 0.5
    assert len(options) == 8
    expensive, _ = plan_setback(_inputs(return_penalty=5000), max_setback=4, min_saving=0.5)
    assert expensive is None


def test_no_setback_when_it_is_warm_outside() -> None:
    assert plan_setback(_inputs(outdoor=21), 4, 0.5) == (None, [])


def test_setback_respects_the_lowest_room_temperature() -> None:
    option = simulate_setback(_inputs(time_constant=10, hours=12, min_room_temperature=19), 6.0)
    assert option.lowest_temperature >= 19.0


def test_night_window_over_midnight() -> None:
    start, end = time(22), time(6)
    window = next_window(datetime(2026, 11, 2, 3, 0, tzinfo=TZ), start, end)
    assert window == (datetime(2026, 11, 1, 22, tzinfo=TZ), datetime(2026, 11, 2, 6, tzinfo=TZ))
    window = next_window(datetime(2026, 11, 2, 12, 0, tzinfo=TZ), start, end)
    assert window == (datetime(2026, 11, 2, 22, tzinfo=TZ), datetime(2026, 11, 3, 6, tzinfo=TZ))
    assert in_window(datetime(2026, 11, 2, 23, 0, tzinfo=TZ), start, end)
    assert not in_window(datetime(2026, 11, 2, 7, 0, tzinfo=TZ), start, end)
