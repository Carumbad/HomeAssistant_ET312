"""Number platform for ET312 channel power controls."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CHANNEL_POWER_UI_MAX,
    CHANNEL_POWER_UI_MIN,
    CONF_CONNECTION_TYPE,
    CONNECTION_MQTT,
    DOMAIN,
    MULTI_ADJUST_UI_MAX,
    MULTI_ADJUST_UI_MIN,
)
from .coordinator import ET312DataUpdateCoordinator
from .entity import ET312CoordinatorEntity, ET312DiscoveredEntity
from .mqtt_manager import ET312MqttDiscoveryManager


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
    runtime = hass.data[DOMAIN][entry.entry_id]
    if entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_MQTT:
        coordinator: ET312DataUpdateCoordinator = runtime
        async_add_entities(ET312PowerNumber(coordinator, description) for description in NUMBERS)
        return

    manager: ET312MqttDiscoveryManager = runtime
    known: set[str] = set()

    def add_for_device(device_id: str) -> None:
        if device_id in known:
            return
        known.add(device_id)
        async_add_entities(
            ET312DiscoveredPowerNumber(manager, device_id, description)
            for description in NUMBERS
        )

    for device_id in sorted(manager.devices):
        add_for_device(device_id)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            manager.signal_device_added,
            add_for_device,
        )
    )


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
        self._attr_unique_id = f"{coordinator.device_uid}_{description.key}_number"

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


class ET312DiscoveredPowerNumber(ET312DiscoveredEntity, NumberEntity):
    """Number entity for discovered MQTT ET312 controls."""

    def __init__(
        self,
        manager: ET312MqttDiscoveryManager,
        device_id: str,
        description: ET312NumberDescription,
    ) -> None:
        """Initialize discovered number entity."""
        super().__init__(manager, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}_number"

    @property
    def native_value(self) -> float | None:
        """Return current control value from cached state."""
        state = self.device_state
        if state is None:
            return None
        value = getattr(state, self.entity_description.key)
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        """Publish control update to this ET312 device command topic."""
        if self.entity_description.control == "power":
            payload = {
                "command": "set_channel_power",
                "channel": self.entity_description.channel,
                "value": int(value),
            }
        else:
            payload = {
                "command": "set_multi_adjust",
                "value": int(value),
            }
        await self.manager.async_publish_command(self.device_id, payload)
