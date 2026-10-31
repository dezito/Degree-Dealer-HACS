"""Base entity for Degree Dealer."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import DegreeDealerCoordinator


class DegreeDealerEntity(CoordinatorEntity[DegreeDealerCoordinator]):
    """Common Degree Dealer entity: name from translations, one device per entry."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DegreeDealerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model="Degree Dealer",
            entry_type=DeviceEntryType.SERVICE,
        )
