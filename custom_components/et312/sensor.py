"""Sensor platform for the ET312 integration."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_CONNECTION_TYPE, CONNECTION_MQTT, DOMAIN
from .coordinator import ET312DataUpdateCoordinator
from .entity import ET312CoordinatorEntity, ET312DiscoveredEntity
from .mqtt_manager import ET312MqttDiscoveryManager

SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="status",
        name="Status",
    ),
    SensorEntityDescription(
        key="mode",
        name="Mode",
    ),
    SensorEntityDescription(
        key="power_level_a",
        name="Channel A Power",
        native_unit_of_measurement="%",
        state_class="measurement",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="power_level_b",
        name="Channel B Power",
        native_unit_of_measurement="%",
        state_class="measurement",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="battery_percent",
        name="Battery",
        device_class="battery",
        native_unit_of_measurement="%",
        state_class="measurement",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="multi_adjust",
        name="Multi Adjust",
        native_unit_of_measurement="%",
        state_class="measurement",
        suggested_display_precision=0,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ET312 sensors."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    if entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_MQTT:
        coordinator: ET312DataUpdateCoordinator = runtime
        async_add_entities([ET312Sensor(coordinator, description) for description in SENSORS])
        return

    manager: ET312MqttDiscoveryManager = runtime
    known: set[str] = set()

    @callback
    def add_for_device(device_id: str) -> None:
        if device_id in known:
            return
        known.add(device_id)
        async_add_entities(
            [ET312DiscoveredSensor(manager, device_id, description) for description in SENSORS]
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            manager.signal_device_added,
            add_for_device,
        )
    )

    for device_id in sorted(manager.devices):
        add_for_device(device_id)


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
        self._attr_unique_id = f"{coordinator.device_uid}_{description.key}"

    @property
    def native_value(self) -> str | int | None:
        """Return the sensor state."""
        if self.entity_description.key == "status":
            return "online" if self.coordinator.data.connected else "offline"
        return getattr(self.coordinator.data, self.entity_description.key)


class ET312DiscoveredSensor(ET312DiscoveredEntity, SensorEntity):
    """Sensor for a dynamically discovered MQTT ET312 device."""

    def __init__(
        self,
        manager: ET312MqttDiscoveryManager,
        device_id: str,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize discovered sensor."""
        super().__init__(manager, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def native_value(self) -> str | int | None:
        """Return sensor value from cached ET312 state."""
        state = self.device_state
        if state is None:
            return None
        if self.entity_description.key == "status":
            return "online" if state.connected else "offline"
        return getattr(state, self.entity_description.key)

    @property
    def available(self) -> bool:
        """Keep the status sensor visible when the ET312 reports offline."""
        if self.entity_description.key == "status":
            return self.device_state is not None
        return super().available
