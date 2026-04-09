"""Select platform for ET312 routine selection."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ROUTINES
from .coordinator import ET312DataUpdateCoordinator
from .entity import ET312CoordinatorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ET312 select entities."""
    coordinator: ET312DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ET312ModeSelect(coordinator)])


class ET312ModeSelect(ET312CoordinatorEntity, SelectEntity):
    """Select entity for ET312 mode selection."""

    _attr_name = "Routine"

    def __init__(self, coordinator: ET312DataUpdateCoordinator) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_mode_select"
        self._attr_options = [ROUTINES[code] for code in sorted(ROUTINES)]

    @property
    def current_option(self) -> str | None:
        """Return the currently selected mode."""
        mode = self.coordinator.data.mode
        if mode in self.options:
            return mode
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the ET312 routine."""
        await self.coordinator.client.async_set_mode(option)
        await self.coordinator.async_request_refresh()
