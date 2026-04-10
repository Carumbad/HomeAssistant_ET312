"""The ET312 integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_MQTT_AVAILABILITY_TOPIC,
    CONF_MQTT_COMMAND_TOPIC,
    CONF_MQTT_TOPIC_PREFIX,
    CONF_MQTT_STATE_TOPIC,
    CONNECTION_MQTT,
    DOMAIN,
)
from .topics import (
    extract_prefix_from_state_topic,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS: Final = ["sensor", "select", "number", "switch"]
_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate ET312 config entries to include stable per-device metadata."""
    if entry.version > 3:
        _LOGGER.error("Cannot migrate ET312 config entry version %s", entry.version)
        return False

    if entry.version < 2:
        data = dict(entry.data)
        if data.get(CONF_CONNECTION_TYPE) == CONNECTION_MQTT:
            state_topic = data.get(CONF_MQTT_STATE_TOPIC)
            if isinstance(state_topic, str):
                inferred_prefix = extract_prefix_from_state_topic(state_topic)
                if inferred_prefix:
                    data.setdefault(CONF_MQTT_TOPIC_PREFIX, inferred_prefix)
        hass.config_entries.async_update_entry(entry, data=data, version=2)
        entry = hass.config_entries.async_get_entry(entry.entry_id) or entry

    if entry.version < 3:
        data = dict(entry.data)
        if data.get(CONF_CONNECTION_TYPE) == CONNECTION_MQTT:
            topic_prefix = str(data.get(CONF_MQTT_TOPIC_PREFIX, "et312")).strip("/")
            data[CONF_MQTT_TOPIC_PREFIX] = topic_prefix
            data[CONF_MQTT_STATE_TOPIC] = f"{topic_prefix}/+/state"
            data[CONF_MQTT_COMMAND_TOPIC] = f"{topic_prefix}/+/command"
            data[CONF_MQTT_AVAILABILITY_TOPIC] = f"{topic_prefix}/+/availability"
        hass.config_entries.async_update_entry(entry, data=data, version=3)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ET312 from a config entry."""
    from .coordinator import ET312DataUpdateCoordinator
    from .et312 import ET312Client, ET312ConnectionConfig
    from .mqtt_manager import ET312MqttDiscoveryManager

    if entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_MQTT:
        manager = ET312MqttDiscoveryManager(hass, entry)
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = manager
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        await manager.async_start()
        return True
    else:
        client = ET312Client(ET312ConnectionConfig.from_mapping(entry.data), hass=hass)
        coordinator = ET312DataUpdateCoordinator(hass, client=client, entry=entry)
        await coordinator.async_config_entry_first_refresh()
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an ET312 config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime = hass.data[DOMAIN].pop(entry.entry_id)
        if entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_MQTT:
            await runtime.async_stop()
        else:
            await runtime.client.async_disconnect()

    return unload_ok
