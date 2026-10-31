"""Degree Dealer - district heating optimiser for Home Assistant."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DASHBOARD, PLATFORMS
from .coordinator import DegreeDealerCoordinator
from .dashboard import async_remove_dashboard, async_setup_dashboard

type DegreeDealerConfigEntry = ConfigEntry[DegreeDealerCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: DegreeDealerConfigEntry) -> bool:
    coordinator = DegreeDealerCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if coordinator.options[CONF_DASHBOARD]:
        # after the platforms, so the sensors' entity ids are in the registry
        await async_setup_dashboard(hass, entry, coordinator.options)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DegreeDealerConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_save()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: DegreeDealerConfigEntry) -> None:
    """Delete what was learnt about the house and the edited dashboard together with the entry."""
    await DegreeDealerCoordinator(hass, entry).async_remove_store()
    await async_remove_dashboard(hass, entry.entry_id)


async def _async_reload(hass: HomeAssistant, entry: DegreeDealerConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
