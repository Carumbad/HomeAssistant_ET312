"""Number platform for ET312 channel power controls."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CHANNEL_POWER_UI_MAX,
    CHANNEL_POWER_UI_MIN,
    DOMAIN,
    MULTI_ADJUST_UI_MAX,
    MULTI_ADJUST_UI_MIN,
)
from .coordinator import ET312DataUpdateCoordinator
from .entity import ET312CoordinatorEntity


@dataclass(frozen=True, kw_only=True)
class ET312NumberDescription(NumberEntityDescription):
    """ET312 number entity description."""

    channel: str | None = None
    control: str = "power"


NUMBERS: tuple[ET312NumberDescription, ...] = (
    ET312NumberDescription(
        key="power_level_a",
        name="Channel A Power Setpoint",
        native_min_value=CHANNEL_POWER_UI_MIN,
        native_max_value=CHANNEL_POWER_UI_MAX,
        native_step=1,
        channel="a",
    ),
    ET312NumberDescription(
        key="power_level_b",
        name="Channel B Power Setpoint",
        native_min_value=CHANNEL_POWER_UI_MIN,
        native_max_value=CHANNEL_POWER_UI_MAX,
        native_step=1,
        channel="b",
    ),
    ET312NumberDescription(
        key="multi_adjust",
        name="Multi Adjust",
        native_min_value=MULTI_ADJUST_UI_MIN,
        native_max_value=MULTI_ADJUST_UI_MAX,
        native_step=1,
        control="multi_adjust",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ET312 number entities."""
    coordinator: ET312DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(ET312PowerNumber(coordinator, description) for description in NUMBERS)


class ET312PowerNumber(ET312CoordinatorEntity, NumberEntity):
    """Number entity for ET312 controls."""

    def __init__(
        self,
        coordinator: ET312DataUpdateCoordinator,
        description: ET312NumberDescription,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}_number"

    @property
    def native_value(self) -> float | None:
        """Return the current control value."""
        value = getattr(self.coordinator.data, self.entity_description.key)
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        """Set the ET312 control value."""
        if self.entity_description.control == "power":
            await self.coordinator.client.async_set_channel_power(
                self.entity_description.channel or "",
                int(value),
            )
        else:
            await self.coordinator.client.async_set_multi_adjust(int(value))
        await self.coordinator.async_request_refresh()
