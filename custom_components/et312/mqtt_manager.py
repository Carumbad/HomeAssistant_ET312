"""Dynamic MQTT discovery manager for ET312 devices."""

from __future__ import annotations

import json
from dataclasses import replace
import logging
from typing import Any, Callable

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_MQTT_TOPIC_PREFIX,
    DEFAULT_MQTT_TOPIC_PREFIX,
    DOMAIN,
    SIGNAL_DEVICE_ADDED,
    SIGNAL_DEVICE_UPDATED,
)
from .et312 import ET312State
from .mqtt_payload import payload_to_text
from .topics import normalize_device_id

_LOGGER = logging.getLogger(__name__)


class ET312MqttDiscoveryManager:
    """Track ET312 devices discovered via wildcard MQTT topics."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize dynamic MQTT manager."""
        self.hass = hass
        self.entry = entry
        self.topic_prefix = str(
            entry.data.get(CONF_MQTT_TOPIC_PREFIX, DEFAULT_MQTT_TOPIC_PREFIX)
        ).strip("/")
        self.devices: dict[str, ET312State] = {}
        self._state_unsub: Callable[[], None] | None = None
        self._availability_unsub: Callable[[], None] | None = None

    @property
    def signal_device_added(self) -> str:
        """Return entry-scoped signal for new devices."""
        return f"{SIGNAL_DEVICE_ADDED}_{self.entry.entry_id}"

    @property
    def signal_device_updated(self) -> str:
        """Return entry-scoped signal for state updates."""
        return f"{SIGNAL_DEVICE_UPDATED}_{self.entry.entry_id}"

    def state_topic(self, device_id: str) -> str:
        """Return state topic path for a specific device."""
        return f"{self.topic_prefix}/{normalize_device_id(device_id)}/state"

    def command_topic(self, device_id: str) -> str:
        """Return command topic path for a specific device."""
        return f"{self.topic_prefix}/{normalize_device_id(device_id)}/command"

    async def async_start(self) -> None:
        """Start wildcard subscriptions for ET312 device discovery."""
        state_pattern = f"{self.topic_prefix}/+/state"
        availability_pattern = f"{self.topic_prefix}/+/availability"
        self._state_unsub = await mqtt.async_subscribe(
            self.hass,
            state_pattern,
            self._state_message_received,
        )
        self._availability_unsub = await mqtt.async_subscribe(
            self.hass,
            availability_pattern,
            self._availability_message_received,
        )

    async def async_stop(self) -> None:
        """Stop MQTT discovery subscriptions."""
        if self._state_unsub is not None:
            self._state_unsub()
            self._state_unsub = None
        if self._availability_unsub is not None:
            self._availability_unsub()
            self._availability_unsub = None

    async def async_publish_command(self, device_id: str, payload: dict[str, Any]) -> None:
        """Publish a command to a discovered ET312 topic."""
        await mqtt.async_publish(
            self.hass,
            self.command_topic(device_id),
            json.dumps(payload),
            qos=0,
            retain=False,
        )

    async def async_request_state(self, device_id: str) -> None:
        """Ask bridge to push latest state for a discovered ET312."""
        await self.async_publish_command(device_id, {"command": "request_state"})

    def _topic_device_id(self, topic: str, suffix: str) -> str | None:
        """Parse device id from topic prefix/device_id/suffix."""
        prefix = f"{self.topic_prefix}/"
        ending = f"/{suffix}"
        if not topic.startswith(prefix) or not topic.endswith(ending):
            return None
        device_id = topic[len(prefix) : -len(ending)]
        if not device_id:
            return None
        return normalize_device_id(device_id)

    @staticmethod
    def _default_state() -> ET312State:
        """Return an empty ET312 state shell for newly discovered devices."""
        return ET312State(
            connected=False,
            mode_code=None,
            mode=None,
            power_level_a=None,
            power_level_b=None,
            mode_options=(),
            battery_percent=None,
            multi_adjust=None,
            front_panel_controls_disabled=False,
        )

    @callback
    def _state_message_received(self, msg) -> None:
        """Handle wildcard state updates from all ET312 MQTT bridges."""
        device_id = self._topic_device_id(msg.topic, "state")
        if device_id is None:
            return
        try:
            payload = json.loads(payload_to_text(msg.payload))
        except (TypeError, json.JSONDecodeError):
            _LOGGER.debug("Ignoring invalid ET312 state payload on %s", msg.topic)
            return

        state = ET312State.from_dict(payload)
        is_new = device_id not in self.devices
        self.devices[device_id] = state
        if is_new:
            async_dispatcher_send(self.hass, self.signal_device_added, device_id)
        async_dispatcher_send(self.hass, self.signal_device_updated, device_id)

    @callback
    def _availability_message_received(self, msg) -> None:
        """Handle wildcard availability updates from all ET312 MQTT bridges."""
        device_id = self._topic_device_id(msg.topic, "availability")
        if device_id is None:
            return
        text = payload_to_text(msg.payload).strip().lower()
        is_online = text == "online"
        existing = self.devices.get(device_id, self._default_state())
        updated = replace(existing, connected=is_online)
        is_new = device_id not in self.devices
        self.devices[device_id] = updated
        if is_new:
            async_dispatcher_send(self.hass, self.signal_device_added, device_id)
        async_dispatcher_send(self.hass, self.signal_device_updated, device_id)
