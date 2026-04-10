"""Entity helpers for the ET312 integration."""

from __future__ import annotations

from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ET312DataUpdateCoordinator
from .et312 import ET312State
from .mqtt_manager import ET312MqttDiscoveryManager


class ET312CoordinatorEntity(CoordinatorEntity[ET312DataUpdateCoordinator]):
    """Base entity class for ET312 entities."""

    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        """Return whether the ET312 is currently available."""
        if not super().available:
            return False
        data = self.coordinator.data
        return data is not None and data.connected

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the ET312."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device_uid)},
            manufacturer="ErosTek",
            model="ET312",
            name=self.coordinator.entry.title,
        )


class ET312DiscoveredEntity(Entity):
    """Base entity for dynamically discovered MQTT ET312 devices."""

    _attr_has_entity_name = True

    def __init__(self, manager: ET312MqttDiscoveryManager, device_id: str) -> None:
        """Initialize discovered ET312 entity."""
        self.manager = manager
        self.device_id = device_id
        self._unsub_dispatcher = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to manager update signals."""
        self._unsub_dispatcher = async_dispatcher_connect(
            self.hass,
            self.manager.signal_device_updated,
            self._handle_manager_update,
        )
        await self.manager.async_request_state(self.device_id)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from manager update signals."""
        if self._unsub_dispatcher is not None:
            self._unsub_dispatcher()
            self._unsub_dispatcher = None

    @property
    def available(self) -> bool:
        """Return whether the discovered ET312 is available."""
        state = self.device_state
        return state is not None and state.connected

    @property
    def device_state(self) -> ET312State | None:
        """Return cached manager state for this device."""
        return self.manager.devices.get(self.device_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device-info metadata for this discovered ET312."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.device_id)},
            manufacturer="ErosTek",
            model="ET312",
            name=self.device_id,
        )

    def _handle_manager_update(self, updated_device_id: str) -> None:
        """Write entity state when manager publishes an update."""
        if updated_device_id != self.device_id:
            return
        self.async_write_ha_state()
