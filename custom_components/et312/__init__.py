"""The ET312 integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS: Final = ["sensor", "select", "number", "switch"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ET312 from a config entry."""
    from .coordinator import ET312DataUpdateCoordinator
    from .et312 import ET312Client, ET312ConnectionConfig

    client = ET312Client(ET312ConnectionConfig.from_mapping(entry.data), hass=hass)
    coordinator = ET312DataUpdateCoordinator(hass, client=client, entry=entry)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an ET312 config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.async_disconnect()

    return unload_ok
