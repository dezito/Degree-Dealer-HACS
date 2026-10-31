"""Heating model: pure calculations without Home Assistant, so they are easy to test.

Units: temperatures in °C, temperature differences in K, time in hours, energy in kWh,
heat loss coefficient (UA) in W/K and money in the configured currency.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
import math

# heat loss fit
MIN_FIT_DAYS = 7
MIN_FIT_SPREAD = 2.0  # K between the mildest and the coldest day

# supply advice: valve demand (%) of the most demanding room
DEMAND_VERY_LOW = 30.0
DEMAND_LOW = 50.0
DEMAND_HIGH = 90.0
STARVED_MINUTES = 30.0

SIMULATION_STEP = 0.1  # h


@dataclass(frozen=True, slots=True)
class HeatLossFit:
    """Daily energy = UA * 24 h * (indoor - outdoor) + base load (hot water, losses)."""

    ua: float
    base_kwh_per_day: float
    days: int
    r2: float


def fit_heat_loss(samples: list[tuple[float, float]]) -> HeatLossFit | None:
    """Least squares fit of (mean indoor - mean outdoor in K, energy in kWh) per day."""
    points = [(dt, energy) for dt, energy in samples if dt > 0 and energy >= 0]
    if len(points) < MIN_FIT_DAYS:
        return None
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    if max(xs) - min(xs) < MIN_FIT_SPREAD:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / sxx
    if slope <= 0:
        return None
    intercept = mean_y - slope * mean_x
    if intercept < 0:
        # a negative base load is noise: fit through the origin instead
        slope = sum(x * y for x, y in points) / sum(x * x for x in xs)
        intercept = 0.0
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return HeatLossFit(ua=slope * 1000 / 24, base_kwh_per_day=intercept, days=len(points), r2=r2)


def cooling_time_constant(start_temp: float, end_temp: float, outdoor: float, hours: float) -> float | None:
    """Time constant (h) from Newton cooling: T(t) = Tout + (T0 - Tout) * e^(-t / tau)."""
    above_start = start_temp - outdoor
    above_end = end_temp - outdoor
    if hours <= 0 or above_end <= 0.5 or above_start <= above_end:
        return None
    return hours / math.log(above_start / above_end)


@dataclass(frozen=True, slots=True)
class RoomDemand:
    """Smoothed valve demand (0-100 %) of one room."""

    name: str
    demand: float | None
    cold_minutes: float = 0.0
    exclude: bool = False


@dataclass(frozen=True, slots=True)
class SupplyAdvice:
    action: str  # raise / lower / hold
    target: float
    delta: float
    reason: str
    key_room: str | None
    key_demand: float | None


def advise_supply(base: float | None, rooms: list[RoomDemand], supply_min: float, supply_max: float) -> SupplyAdvice | None:
    """Lowest supply temperature that keeps the most demanding room warm.

    The valves should be mostly open: then the water gives off most of its heat before it
    returns. Mostly closed valves mean the supply is hotter than the house needs.
    """
    rooms = [room for room in rooms if not room.exclude and room.demand is not None]
    if base is None or not rooms:
        return None
    key = max(rooms, key=lambda room: room.demand or 0.0)
    key_demand = key.demand or 0.0
    starved = [room for room in rooms if room.cold_minutes >= STARVED_MINUTES and (room.demand or 0.0) >= DEMAND_HIGH]
    if starved:
        delta, reason = (2.0 if len(starved) > 1 else 1.0), "rooms_starved"
    elif key_demand < DEMAND_VERY_LOW:
        delta, reason = -2.0, "valves_mostly_closed"
    elif key_demand < DEMAND_LOW:
        delta, reason = -1.0, "valves_partly_closed"
    else:
        delta, reason = 0.0, "in_band"
    target = min(max(base + delta, supply_min), supply_max)
    if delta and target == base:
        reason = "at_limit"
    delta = round(target - base, 1)
    action = "raise" if delta > 0 else "lower" if delta < 0 else "hold"
    return SupplyAdvice(action, round(target, 1), delta, reason, key.name, round(key_demand, 1))


@dataclass(frozen=True, slots=True)
class SetbackInputs:
    comfort: float
    outdoor: float
    hours: float
    ua: float
    time_constant: float
    heat_up_rate: float
    heat_price: float
    return_penalty: float  # money per K return temperature per MWh
    recovery_return_rise: float  # K the return rises while recovering
    min_room_temperature: float


@dataclass(frozen=True, slots=True)
class SetbackOption:
    depth: float
    lowest_temperature: float
    saving_kwh: float
    saving_cost: float
    penalty_cost: float
    recovery_hours: float

    @property
    def net(self) -> float:
        return self.saving_cost - self.penalty_cost


def simulate_setback(inputs: SetbackInputs, depth: float) -> SetbackOption:
    """Let the house cool to comfort - depth and heat it back up just in time.

    The saving is the lower heat loss while the house is cooler (UA * K-hours below comfort).
    The heat stored in the house is put back during recovery, which raises the return
    temperature; with a return/cooling tariff that costs money.
    """
    comfort = inputs.comfort
    floor = min(max(comfort - depth, inputs.min_room_temperature), comfort)
    decay = math.exp(-SIMULATION_STEP / inputs.time_constant)
    steps = max(round(inputs.hours / SIMULATION_STEP), 0)
    temp = lowest = comfort
    kelvin_hours = 0.0
    recovery_start: float | None = None
    for step in range(steps):
        remaining = inputs.hours - step * SIMULATION_STEP
        if (comfort - temp) / inputs.heat_up_rate >= remaining - 1e-9 and temp < comfort:
            if recovery_start is None:
                recovery_start = step * SIMULATION_STEP
            temp = min(comfort, temp + inputs.heat_up_rate * SIMULATION_STEP)
        else:
            temp = max(floor, inputs.outdoor + (temp - inputs.outdoor) * decay) if temp > inputs.outdoor else temp
        lowest = min(lowest, temp)
        kelvin_hours += (comfort - temp) * SIMULATION_STEP
    recovery_hours = inputs.hours - recovery_start if recovery_start is not None else 0.0
    saving_kwh = inputs.ua * kelvin_hours / 1000
    # stored heat (C = UA * tau) plus the normal heat loss while recovering
    recovery_kwh = (
        inputs.ua * inputs.time_constant * (comfort - lowest)
        + inputs.ua * max(comfort - inputs.outdoor, 0.0) * recovery_hours
    ) / 1000
    penalty = recovery_kwh / 1000 * inputs.recovery_return_rise * inputs.return_penalty
    return SetbackOption(
        depth=depth,
        lowest_temperature=round(lowest, 2),
        saving_kwh=round(saving_kwh, 3),
        saving_cost=round(saving_kwh * inputs.heat_price, 2),
        penalty_cost=round(penalty, 2),
        recovery_hours=round(recovery_hours, 2),
    )


def plan_setback(
    inputs: SetbackInputs, max_setback: float, min_saving: float, step: float = 0.5
) -> tuple[SetbackOption | None, list[SetbackOption]]:
    """The most profitable setback depth, or None when no depth saves at least min_saving."""
    if inputs.hours <= 0 or inputs.outdoor >= inputs.comfort - 0.5:
        return None, []
    options = []
    depth = step
    while depth <= max_setback + 1e-9:
        options.append(simulate_setback(inputs, round(depth, 2)))
        depth += step
    worth_it = [option for option in options if option.net >= min_saving]
    best = max(worth_it, key=lambda option: option.net) if worth_it else None
    return best, options


def next_window(now: datetime, start: time, end: time) -> tuple[datetime, datetime]:
    """The window that is running now, or the next one (the end may be after midnight)."""
    tz = now.tzinfo
    today = datetime.combine(now.date(), start, tzinfo=tz)
    for window_start in (today - timedelta(days=1), today, today + timedelta(days=1)):
        window_end = datetime.combine(window_start.date(), end, tzinfo=tz)
        if window_end <= window_start:
            window_end += timedelta(days=1)
        if now < window_end:
            return window_start, window_end
    raise AssertionError("unreachable")


def in_window(now: datetime, start: time, end: time) -> bool:
    window_start, window_end = next_window(now, start, end)
    return window_start <= now < window_end
