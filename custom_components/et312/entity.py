"""Entity helpers for the ET312 integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ET312DataUpdateCoordinator


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
            identifiers={(DOMAIN, self.coordinator.entry.entry_id)},
            manufacturer="ErosTek",
            model="ET312",
            name=self.coordinator.entry.title,
        )
