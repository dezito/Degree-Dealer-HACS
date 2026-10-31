"""Button that starts over from the recorder history."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DegreeDealerConfigEntry
from .entity import DegreeDealerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: DegreeDealerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([LearnFromHistoryButton(entry.runtime_data)])


class LearnFromHistoryButton(DegreeDealerEntity, ButtonEntity):
    """Forget what was learnt and learn again from everything the recorder has."""

    def __init__(self, coordinator) -> None:  # type: ignore[no-untyped-def]
        super().__init__(coordinator, "learn_from_history")

    @property
    def available(self) -> bool:
        return not self.coordinator.history_running

    async def async_press(self) -> None:
        self.coordinator.entry.async_create_background_task(
            self.hass, self.coordinator.async_learn_from_history(), "degree_dealer learn from history"
        )
