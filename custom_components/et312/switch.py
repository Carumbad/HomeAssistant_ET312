"""Switch platform for ET312 front-panel control flags."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_CONNECTION_TYPE, CONNECTION_MQTT, DOMAIN
from .coordinator import ET312DataUpdateCoordinator
from .entity import ET312CoordinatorEntity, ET312DiscoveredEntity
from .mqtt_manager import ET312MqttDiscoveryManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ET312 switch entities."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    if entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_MQTT:
        coordinator: ET312DataUpdateCoordinator = runtime
        async_add_entities([ET312DisableFrontPanelControlsSwitch(coordinator)])
        return

    manager: ET312MqttDiscoveryManager = runtime
    known: set[str] = set()

    @callback
    def add_for_device(device_id: str) -> None:
        if device_id in known:
            return
        known.add(device_id)
        async_add_entities([ET312DiscoveredDisableFrontPanelControlsSwitch(manager, device_id)])

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            manager.signal_device_added,
            add_for_device,
        )
    )

    for device_id in sorted(manager.devices):
        add_for_device(device_id)


class ET312DisableFrontPanelControlsSwitch(ET312CoordinatorEntity, SwitchEntity):
    """Switch for ET312 front-panel knob control."""

    _attr_name = "Disable Front Panel Controls"
    _attr_icon = "mdi:tune-vertical-variant"

    def __init__(self, coordinator: ET312DataUpdateCoordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{coordinator.device_uid}_disable_front_panel_controls"
        )

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


class ET312DiscoveredDisableFrontPanelControlsSwitch(ET312DiscoveredEntity, SwitchEntity):
    """Switch for discovered ET312 front-panel knob control."""

    _attr_name = "Disable Front Panel Controls"
    _attr_icon = "mdi:tune-vertical-variant"

    def __init__(self, manager: ET312MqttDiscoveryManager, device_id: str) -> None:
        """Initialize discovered switch."""
        super().__init__(manager, device_id)
        self._attr_unique_id = f"{device_id}_disable_front_panel_controls"

    @property
    def is_on(self) -> bool:
        """Return whether front-panel controls are disabled."""
        state = self.device_state
        if state is None:
            return False
        return state.front_panel_controls_disabled

    async def async_turn_on(self, **kwargs) -> None:
        """Publish disable command."""
        await self.manager.async_publish_command(
            self.device_id,
            {"command": "set_front_panel_controls_disabled", "value": True},
        )

    async def async_turn_off(self, **kwargs) -> None:
        """Publish enable command."""
        await self.manager.async_publish_command(
            self.device_id,
            {"command": "set_front_panel_controls_disabled", "value": False},
        )
