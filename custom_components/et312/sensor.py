"""Sensor platform for the ET312 integration."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ET312DataUpdateCoordinator
from .entity import ET312CoordinatorEntity

SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="mode",
        name="Mode",
    ),
    SensorEntityDescription(
        key="power_level_a",
        name="Channel A Power",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="power_level_b",
        name="Channel B Power",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="battery_percent",
        name="Battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="%",
    ),
    SensorEntityDescription(
        key="multi_adjust",
        name="Multi Adjust",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ET312 sensors."""
    coordinator: ET312DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(ET312Sensor(coordinator, description) for description in SENSORS)


class ET312Sensor(ET312CoordinatorEntity, SensorEntity):
    """Representation of an ET312 sensor."""

    def __init__(
        self,
        coordinator: ET312DataUpdateCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> str | int | None:
        """Return the sensor state."""
        return getattr(self.coordinator.data, self.entity_description.key)
