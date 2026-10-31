"""Sensors with what Degree Dealer measures, learns and advises."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DegreeDealerConfigEntry
from .const import DOMAIN, MANUFACTURER
from .coordinator import DegreeDealerCoordinator, DegreeDealerData


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _difference(first: float | None, second: float | None) -> float | None:
    return None if first is None or second is None else round(first - second, 1)


def _rooms(data: DegreeDealerData) -> dict[str, Any]:
    return {"rooms": [room.as_dict() for room in data.rooms]}


def _advice(data: DegreeDealerData) -> dict[str, Any]:
    advice = data.advice
    return {
        "action": advice.action if advice else None,
        "delta": advice.delta if advice else None,
        "reason": advice.reason if advice else None,
        "key_room": advice.key_room if advice else None,
        "key_demand": advice.key_demand if advice else None,
        "supply_base": data.supply_base,
        "cold_rooms": [room.name for room in data.rooms if room.cold_minutes > 0],
    }


def _return(data: DegreeDealerData) -> dict[str, Any]:
    return {"target": data.return_target, "target_source": data.return_target_source}


def _heat_loss(data: DegreeDealerData) -> dict[str, Any]:
    fit = data.heat_loss
    return {
        "source": data.ua_source,
        "learnt_value": _round(fit.ua) if fit else None,
        "base_load_kwh_per_day": _round(fit.base_kwh_per_day, 2) if fit else None,
        "days_used": fit.days if fit else 0,
        "r2": _round(fit.r2, 3) if fit else None,
    }


def _setback_depth(data: DegreeDealerData) -> float | None:
    """0 when a setback does not pay, unknown while the house model is still being learnt."""
    if data.setback_missing:
        return None
    return data.setback.depth if data.setback else 0.0


def _setback(data: DegreeDealerData) -> dict[str, Any]:
    best = data.setback
    window = data.window
    recovery_start = None
    if best and window and best.recovery_hours:
        recovery_start = (window[1] - timedelta(hours=best.recovery_hours)).isoformat()
    return {
        "worth_it": best is not None,
        "window_start": window[0].isoformat() if window else None,
        "window_end": window[1].isoformat() if window else None,
        "comfort_temperature": _round(data.comfort),
        "outdoor_temperature": data.outdoor,
        "recovery_start": recovery_start,
        **({f"best_{key}": value for key, value in asdict(best).items()} if best else {}),
        "best_net": round(best.net, 2) if best else None,
        "options": [{**asdict(option), "net": round(option.net, 2)} for option in data.setback_options],
        "missing": data.setback_missing,
        "heat_up_rate_learnt": data.heat_up_rate_learnt,
    }


@dataclass(frozen=True, kw_only=True)
class DegreeDealerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[DegreeDealerData], StateType]
    attrs_fn: Callable[[DegreeDealerData], dict[str, Any]] | None = None


TEMPERATURE = {
    "device_class": SensorDeviceClass.TEMPERATURE,
    "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
    "state_class": SensorStateClass.MEASUREMENT,
    "suggested_display_precision": 1,
}
KELVIN = {
    "native_unit_of_measurement": UnitOfTemperature.KELVIN,
    "state_class": SensorStateClass.MEASUREMENT,
    "suggested_display_precision": 1,
}

SENSORS: tuple[DegreeDealerSensorDescription, ...] = (
    DegreeDealerSensorDescription(
        key="indoor_temperature", value_fn=lambda data: data.indoor, attrs_fn=_rooms, **TEMPERATURE
    ),
    DegreeDealerSensorDescription(
        key="heat_demand",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: data.key_demand,
        attrs_fn=_rooms,
    ),
    DegreeDealerSensorDescription(
        key="supply_advice", value_fn=lambda data: data.advice.target if data.advice else None, attrs_fn=_advice, **TEMPERATURE
    ),
    DegreeDealerSensorDescription(
        key="supply_return_difference",
        value_fn=lambda data: _difference(data.supply, data.return_temperature),
        **KELVIN,
    ),
    DegreeDealerSensorDescription(
        key="return_excess",
        value_fn=lambda data: _difference(data.return_temperature, data.return_target),
        attrs_fn=_return,
        **KELVIN,
    ),
    DegreeDealerSensorDescription(
        key="heat_loss_coefficient",
        native_unit_of_measurement="W/K",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: _round(data.ua),
        attrs_fn=_heat_loss,
    ),
    DegreeDealerSensorDescription(
        key="time_constant",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: _round(data.time_constant),
    ),
    DegreeDealerSensorDescription(
        key="heat_up_rate",
        native_unit_of_measurement="K/h",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: _round(data.heat_up_rate, 2),
        attrs_fn=lambda data: {"learnt": data.heat_up_rate_learnt},
    ),
    DegreeDealerSensorDescription(
        key="night_setback",
        value_fn=_setback_depth,
        attrs_fn=_setback,
        **KELVIN,
    ),
    DegreeDealerSensorDescription(
        key="learning_days",
        native_unit_of_measurement=UnitOfTime.DAYS,
        value_fn=lambda data: data.learning_days,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: DegreeDealerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(DegreeDealerSensor(coordinator, description) for description in SENSORS)


class DegreeDealerSensor(CoordinatorEntity[DegreeDealerCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: DegreeDealerSensorDescription

    def __init__(self, coordinator: DegreeDealerCoordinator, description: DegreeDealerSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_translation_key = description.key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model="Degree Dealer",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
