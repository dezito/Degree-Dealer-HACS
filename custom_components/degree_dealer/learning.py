"""What Degree Dealer learns about the house over time (stored between restarts)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from .model import cooling_time_constant

DEMAND_SMOOTHING = 0.3  # weight of a new valve demand sample (one per update)
LEARN_SMOOTHING = 0.2  # weight of a new time constant / heat-up rate
COMFORT_SMOOTHING = 0.05

# cooling: the thermostat sits next to the radiator, which is still warm right after the valve closes
COOLING_SETTLE_HOURS = 0.5
COOLING_MIN_HOURS = 2.0
COOLING_MAX_HOURS = 8.0
COOLING_MIN_DROP = 0.3  # K
COOLING_MIN_ABOVE_OUTDOOR = 5.0  # K
TIME_CONSTANT_RANGE = (5.0, 1000.0)  # h

HEATING_MIN_DEMAND = 50.0  # %
HEATING_MIN_HOURS = 0.5
HEATING_MIN_RISE = 0.3  # K
HEAT_UP_RATE_RANGE = (0.05, 10.0)  # K/h

COLD_MARGIN = 0.5  # K below the setpoint before a room counts as cold
MAX_DAYS = 1100  # about three years: the history goes as far back as the recorder has data
MIN_DAY_SAMPLES = 200  # of 288 at one sample per 5 minutes
# warmer days are left out of the heat loss fit: sun and people heat the house, so the
# energy no longer follows the indoor-outdoor difference
HEATING_LIMIT = 12.0  # °C daily mean outdoor temperature


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _blend(old: float | None, new: float, weight: float) -> float:
    return new if old is None else old * (1 - weight) + new * weight


@dataclass
class RoomLearner:
    """Valve demand, cooling time constant and heat-up rate of one room."""

    demand: float | None = None
    cold_since: str | None = None
    cooling: dict[str, Any] | None = None
    heating: dict[str, Any] | None = None
    time_constant: float | None = None
    time_constant_samples: int = 0
    heat_up_rate: float | None = None
    heat_up_rate_samples: int = 0

    def update(
        self,
        now: datetime,
        temperature: float | None,
        setpoint: float | None,
        raw_demand: float | None,
        outdoor: float | None,
        night: bool,
        paused: bool = False,
    ) -> None:
        """paused: a window or door is open, so nothing the room does now says anything about the house."""
        if paused:
            self.cooling = None
            self.heating = None
            self.cold_since = None
            return
        if raw_demand is not None:
            self.demand = _blend(self.demand, raw_demand, DEMAND_SMOOTHING)
        if temperature is None:
            return
        cold = setpoint is not None and temperature < setpoint - COLD_MARGIN
        if not cold:
            self.cold_since = None
        elif self.cold_since is None:
            self.cold_since = now.isoformat()
        self._update_cooling(now, temperature, setpoint, raw_demand, outdoor, night)
        self._update_heating(now, temperature, setpoint, raw_demand)

    def cold_minutes(self, now: datetime) -> float:
        since = _time(self.cold_since)
        return (now - since).total_seconds() / 60 if since else 0.0

    def _update_cooling(
        self,
        now: datetime,
        temperature: float,
        setpoint: float | None,
        raw_demand: float | None,
        outdoor: float | None,
        night: bool,
    ) -> None:
        # only at night: sun and cooking would make the house look better insulated than it is
        idle = raw_demand is not None and raw_demand <= 1.0 and (setpoint is None or temperature >= setpoint - 0.2)
        if not (idle and night and outdoor is not None):
            self._finish_cooling()
            return
        episode = self.cooling
        if episode is None:
            self.cooling = {"start": now.isoformat(), "t0": None, "temp0": None, "outdoor_sum": 0.0, "outdoor_count": 0}
            return
        start = _time(episode["start"])
        if start is None or (now - start).total_seconds() / 3600 > COOLING_MAX_HOURS:
            self._finish_cooling()
            return
        if episode["t0"] is None:
            if (now - start).total_seconds() / 3600 >= COOLING_SETTLE_HOURS:
                episode["t0"] = now.isoformat()
                episode["temp0"] = temperature
            return
        episode["outdoor_sum"] += outdoor
        episode["outdoor_count"] += 1
        episode["t1"] = now.isoformat()
        episode["temp1"] = temperature

    def _finish_cooling(self) -> None:
        episode, self.cooling = self.cooling, None
        if not episode or episode.get("t1") is None or not episode["outdoor_count"]:
            return
        hours = (datetime.fromisoformat(episode["t1"]) - datetime.fromisoformat(episode["t0"])).total_seconds() / 3600
        temp0, temp1 = episode["temp0"], episode["temp1"]
        outdoor = episode["outdoor_sum"] / episode["outdoor_count"]
        if hours < COOLING_MIN_HOURS or temp0 - temp1 < COOLING_MIN_DROP or temp0 - outdoor < COOLING_MIN_ABOVE_OUTDOOR:
            return
        tau = cooling_time_constant(temp0, temp1, outdoor, hours)
        if tau is None or not TIME_CONSTANT_RANGE[0] <= tau <= TIME_CONSTANT_RANGE[1]:
            return
        self.time_constant = _blend(self.time_constant, tau, LEARN_SMOOTHING)
        self.time_constant_samples += 1

    def _update_heating(self, now: datetime, temperature: float, setpoint: float | None, raw_demand: float | None) -> None:
        heating = (
            raw_demand is not None
            and raw_demand >= HEATING_MIN_DEMAND
            and setpoint is not None
            and temperature < setpoint - 0.3
        )
        if heating:
            if self.heating is None:
                self.heating = {"start": now.isoformat(), "temp0": temperature}
            return
        episode, self.heating = self.heating, None
        if not episode:
            return
        # the sample that ends the episode is the one that reached the setpoint
        hours = (now - datetime.fromisoformat(episode["start"])).total_seconds() / 3600
        rise = temperature - episode["temp0"]
        if hours < HEATING_MIN_HOURS or rise < HEATING_MIN_RISE:
            return
        rate = rise / hours
        if HEAT_UP_RATE_RANGE[0] <= rate <= HEAT_UP_RATE_RANGE[1]:
            self.heat_up_rate = _blend(self.heat_up_rate, rate, LEARN_SMOOTHING)
            self.heat_up_rate_samples += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "demand": self.demand,
            "cold_since": self.cold_since,
            "cooling": self.cooling,
            "heating": self.heating,
            "time_constant": self.time_constant,
            "time_constant_samples": self.time_constant_samples,
            "heat_up_rate": self.heat_up_rate,
            "heat_up_rate_samples": self.heat_up_rate_samples,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoomLearner:
        known = {key: data[key] for key in cls().to_dict() if key in data}
        return cls(**known)


@dataclass
class HouseLearner:
    """Daily means and meter readings (for the heat loss fit) plus the rooms."""

    rooms: dict[str, RoomLearner] = field(default_factory=dict)
    days: list[dict[str, Any]] = field(default_factory=list)
    day: str | None = None
    sums: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    comfort: float | None = None
    # result of learning from the recorder history (None = not done yet)
    history: dict[str, Any] | None = None

    def room(self, entity_id: str) -> RoomLearner:
        return self.rooms.setdefault(entity_id, RoomLearner())

    def add_sample(self, now: datetime, values: dict[str, float | None], meter_kwh: float | None) -> None:
        """Add one sample to today's means; the first sample of a new day closes the previous one."""
        today = now.date().isoformat()
        if self.day != today:
            if self.day is not None:
                self._close_day(meter_kwh)
            self.day = today
            self.sums = {}
            self.counts = {}
        for key, value in values.items():
            if value is not None:
                self.sums[key] = self.sums.get(key, 0.0) + value
                self.counts[key] = self.counts.get(key, 0) + 1

    def _close_day(self, meter_kwh: float | None) -> None:
        # the first reading of the new day is the reading at the end of the closed day
        record: dict[str, Any] = {"date": self.day, "meter_kwh": meter_kwh, "samples": self.counts.get("indoor", 0)}
        for key, total in self.sums.items():
            record[key] = round(total / self.counts[key], 3)
        self.days = [day for day in self.days if day["date"] != self.day] + [record]
        self.days = self.days[-MAX_DAYS:]

    def update_comfort(self, setpoint: float) -> None:
        self.comfort = _blend(self.comfort, setpoint, COMFORT_SMOOTHING)

    def heat_loss_samples(self, lag_days: int = 0) -> list[tuple[float, float]]:
        """(indoor - outdoor, kWh) per full day. lag_days: meter data that arrives days late."""
        by_date = {date.fromisoformat(day["date"]): day for day in self.days}
        samples = []
        for day, record in sorted(by_date.items()):
            if record.get("samples", 0) < MIN_DAY_SAMPLES or record.get("indoor") is None or record.get("outdoor") is None:
                continue
            if record["outdoor"] >= HEATING_LIMIT:
                continue
            end = by_date.get(day + timedelta(days=lag_days))
            previous = by_date.get(day + timedelta(days=lag_days - 1))
            if not end or not previous or end.get("meter_kwh") is None or previous.get("meter_kwh") is None:
                continue
            energy = end["meter_kwh"] - previous["meter_kwh"]
            if energy >= 0:
                samples.append((record["indoor"] - record["outdoor"], energy))
        return samples

    def time_constant(self) -> float | None:
        values = [room.time_constant for room in self.rooms.values() if room.time_constant is not None]
        return median(values) if values else None

    def heat_up_rate(self) -> float | None:
        values = [room.heat_up_rate for room in self.rooms.values() if room.heat_up_rate is not None]
        return median(values) if values else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rooms": {entity_id: room.to_dict() for entity_id, room in self.rooms.items()},
            "days": self.days,
            "day": self.day,
            "sums": self.sums,
            "counts": self.counts,
            "comfort": self.comfort,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HouseLearner:
        return cls(
            rooms={entity_id: RoomLearner.from_dict(room) for entity_id, room in data.get("rooms", {}).items()},
            days=list(data.get("days", [])),
            day=data.get("day"),
            sums=dict(data.get("sums", {})),
            counts=dict(data.get("counts", {})),
            comfort=data.get("comfort"),
            history=data.get("history"),
        )
