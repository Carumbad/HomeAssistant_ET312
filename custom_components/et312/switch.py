"""Switch platform for ET312 front-panel control flags."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ET312DataUpdateCoordinator
from .entity import ET312CoordinatorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ET312 switch entities."""
    coordinator: ET312DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ET312DisableFrontPanelControlsSwitch(coordinator)])


class ET312DisableFrontPanelControlsSwitch(ET312CoordinatorEntity, SwitchEntity):
    """Switch for ET312 front-panel knob control."""

    _attr_name = "Disable Front Panel Controls"
    _attr_icon = "mdi:tune-vertical-variant"

    def __init__(self, coordinator: ET312DataUpdateCoordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_disable_front_panel_controls"

    @property
    def is_on(self) -> bool:
        """Return whether the front-panel controls are disabled."""
        return self.coordinator.data.front_panel_controls_disabled

    async def async_turn_on(self, **kwargs) -> None:
        """Disable ET312 front-panel controls."""
        await self.coordinator.client.async_set_front_panel_controls_disabled(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Enable ET312 front-panel controls."""
        await self.coordinator.client.async_set_front_panel_controls_disabled(False)
        await self.coordinator.async_request_refresh()
