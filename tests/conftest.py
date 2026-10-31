"""Shared fixtures: a living room and a bathroom with thermostats, sensors and a heating plant."""
from __future__ import annotations

from typing import Any

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.degree_dealer.const import (
    CONF_OUTDOOR,
    CONF_RETURN,
    CONF_ROOMS,
    CONF_SUPPLY,
    DOMAIN,
    ROOM_AREA,
    ROOM_CLIMATES,
    ROOM_EXCLUDE,
    ROOM_HUMIDITY,
    ROOM_MOTION,
    ROOM_OPENINGS,
    ROOM_TEMPERATURES,
    ROOM_VALVES,
    default_options,
)

SUPPLY = "sensor.ecl_supply"
RETURN = "sensor.ecl_return"
OUTDOOR = "sensor.ecl_outdoor"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Load custom_components/ in every test."""


def set_plant(hass: HomeAssistant, supply: float = 45.0, ret: float = 30.0, outdoor: float = 5.0) -> None:
    hass.states.async_set(SUPPLY, str(supply), {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(RETURN, str(ret), {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, str(outdoor), {"unit_of_measurement": "°C", "device_class": "temperature"})


def set_climate(hass: HomeAssistant, entity_id: str, temperature: float, setpoint: float, action: str = "idle") -> None:
    hass.states.async_set(
        entity_id, "heat", {"current_temperature": temperature, "temperature": setpoint, "hvac_action": action}
    )


def build_rooms(hass: HomeAssistant) -> dict[str, str]:
    """Areas with devices and entities in the registries, as a real installation has them."""
    areas = ar.async_get(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    living = areas.async_create("Living room")
    bath = areas.async_create("Bathroom")

    trv = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test", "trv_living")})
    devices.async_update_device(trv.id, area_id=living.id)
    entities.async_get_or_create("climate", "test", "trv_living", device_id=trv.id, suggested_object_id="living_trv")
    entities.async_get_or_create(
        "sensor", "test", "trv_living_temp", device_id=trv.id, suggested_object_id="living_trv_temperature",
        original_device_class="temperature",
    )
    entities.async_get_or_create(
        "sensor", "test", "living_valve", suggested_object_id="living_heating_power", unit_of_measurement="%"
    )
    entities.async_update_entity("sensor.living_heating_power", area_id=living.id)
    room_sensor = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test", "living_sensor")})
    devices.async_update_device(room_sensor.id, area_id=living.id)
    entities.async_get_or_create(
        "sensor", "test", "living_temp", device_id=room_sensor.id, suggested_object_id="living_temperature",
        original_device_class="temperature",
    )
    entities.async_get_or_create(
        "sensor", "test", "living_humidity", device_id=room_sensor.id, suggested_object_id="living_humidity",
        original_device_class="humidity",
    )
    entities.async_get_or_create(
        "binary_sensor", "test", "living_window", device_id=room_sensor.id, suggested_object_id="living_window",
        original_device_class="window",
    )
    entities.async_get_or_create(
        "binary_sensor", "test", "living_motion", device_id=room_sensor.id, suggested_object_id="living_motion",
        original_device_class="motion",
    )

    bath_trv = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test", "trv_bath")})
    devices.async_update_device(bath_trv.id, area_id=bath.id)
    entities.async_get_or_create("climate", "test", "trv_bath", device_id=bath_trv.id, suggested_object_id="bath_trv")
    return {"living": living.id, "bath": bath.id}


def mock_options(areas: dict[str, str], **overrides: Any) -> dict[str, Any]:
    return {
        **default_options(),
        CONF_SUPPLY: SUPPLY,
        CONF_RETURN: RETURN,
        CONF_OUTDOOR: OUTDOOR,
        CONF_ROOMS: [
            {
                ROOM_AREA: areas["living"],
                ROOM_CLIMATES: ["climate.living_trv"],
                ROOM_TEMPERATURES: ["sensor.living_temperature"],
                ROOM_VALVES: ["sensor.living_heating_power"],
                ROOM_OPENINGS: ["binary_sensor.living_window"],
                ROOM_MOTION: ["binary_sensor.living_motion"],
                ROOM_HUMIDITY: ["sensor.living_humidity"],
                ROOM_EXCLUDE: False,
            },
            {
                ROOM_AREA: areas["bath"],
                ROOM_CLIMATES: ["climate.bath_trv"],
                ROOM_TEMPERATURES: [],
                ROOM_VALVES: [],
                ROOM_OPENINGS: [],
                ROOM_MOTION: [],
                ROOM_HUMIDITY: [],
                ROOM_EXCLUDE: True,
            },
        ],
        **overrides,
    }


@pytest.fixture
def areas(hass: HomeAssistant) -> dict[str, str]:
    return build_rooms(hass)


@pytest.fixture
def entry(hass: HomeAssistant, areas: dict[str, str]) -> MockConfigEntry:
    config_entry = MockConfigEntry(domain=DOMAIN, title="Degree Dealer", data={}, options=mock_options(areas))
    config_entry.add_to_hass(hass)
    return config_entry
