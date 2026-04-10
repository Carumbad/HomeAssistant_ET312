"""Select platform for ET312 routine selection."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_CONNECTION_TYPE, CONNECTION_MQTT, DOMAIN, ROUTINES
from .coordinator import ET312DataUpdateCoordinator
from .entity import ET312CoordinatorEntity, ET312DiscoveredEntity
from .mqtt_manager import ET312MqttDiscoveryManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ET312 select entities."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    if entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_MQTT:
        coordinator: ET312DataUpdateCoordinator = runtime
        async_add_entities([ET312ModeSelect(coordinator)])
        return

    manager: ET312MqttDiscoveryManager = runtime
    known: set[str] = set()

    def add_for_device(device_id: str) -> None:
        if device_id in known:
            return
        known.add(device_id)
        async_add_entities([ET312DiscoveredModeSelect(manager, device_id)])

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            manager.signal_device_added,
            add_for_device,
        )
    )

    for device_id in sorted(manager.devices):
        add_for_device(device_id)


class ET312ModeSelect(ET312CoordinatorEntity, SelectEntity):
    """Select entity for ET312 mode selection."""

    _attr_name = "Routine"

    def __init__(self, coordinator: ET312DataUpdateCoordinator) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_uid}_mode_select"
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


class ET312DiscoveredModeSelect(ET312DiscoveredEntity, SelectEntity):
    """Mode select entity for a discovered MQTT ET312 device."""

    _attr_name = "Routine"

    def __init__(self, manager: ET312MqttDiscoveryManager, device_id: str) -> None:
        """Initialize discovered mode select."""
        super().__init__(manager, device_id)
        self._attr_unique_id = f"{device_id}_mode_select"
        self._attr_options = [ROUTINES[code] for code in sorted(ROUTINES)]

    @property
    def current_option(self) -> str | None:
        """Return currently selected routine."""
        state = self.device_state
        if state is None:
            return None
        mode = state.mode
        if mode in self.options:
            return mode
        return None

    async def async_select_option(self, option: str) -> None:
        """Publish routine change command for this ET312."""
        await self.manager.async_publish_command(
            self.device_id,
            {"command": "set_mode", "mode": option},
        )
