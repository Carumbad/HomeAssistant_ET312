"""Config flow for the ET312 integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_BAUDRATE,
    CONF_CONNECTION_TYPE,
    CONF_DEVICE,
    CONF_MQTT_AVAILABILITY_TOPIC,
    CONF_MQTT_COMMAND_TOPIC,
    CONF_MQTT_STATE_TOPIC,
    CONF_TIMEOUT,
    CONNECTION_MQTT,
    CONNECTION_SERIAL,
    DEFAULT_BAUDRATE,
    DEFAULT_MQTT_AVAILABILITY_TOPIC,
    DEFAULT_MQTT_COMMAND_TOPIC,
    DEFAULT_MQTT_STATE_TOPIC,
    DEFAULT_NAME,
    DEFAULT_TIMEOUT,
    DOMAIN,
)
from .et312 import ET312Client, ET312ConnectionConfig, ET312ConnectionError


class ET312ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ET312."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Choose the ET312 connection type."""
        if user_input is not None:
            if user_input[CONF_CONNECTION_TYPE] == CONNECTION_SERIAL:
                return await self.async_step_serial()
            if user_input[CONF_CONNECTION_TYPE] == CONNECTION_MQTT:
                return await self.async_step_mqtt()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONNECTION_TYPE, default=CONNECTION_SERIAL): vol.In(
                        [CONNECTION_SERIAL, CONNECTION_MQTT]
                    ),
                }
            ),
        )

    async def async_step_serial(
        self, user_input: dict[str, str | int | float] | None = None
    ) -> FlowResult:
        """Configure a direct serial connection."""
        return await self._async_handle_connection_step(
            user_input=user_input,
            connection_type=CONNECTION_SERIAL,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE): str,
                    vol.Optional(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): int,
                    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): vol.Coerce(float),
                }
            ),
        )

    async def async_step_mqtt(
        self, user_input: dict[str, str | int | float] | None = None
    ) -> FlowResult:
        """Configure an MQTT bridge connection."""
        return await self._async_handle_connection_step(
            user_input=user_input,
            connection_type=CONNECTION_MQTT,
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_MQTT_STATE_TOPIC, default=DEFAULT_MQTT_STATE_TOPIC
                    ): str,
                    vol.Optional(
                        CONF_MQTT_COMMAND_TOPIC, default=DEFAULT_MQTT_COMMAND_TOPIC
                    ): str,
                    vol.Optional(
                        CONF_MQTT_AVAILABILITY_TOPIC,
                        default=DEFAULT_MQTT_AVAILABILITY_TOPIC,
                    ): str,
                    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): vol.Coerce(float),
                }
            ),
        )

    async def _async_handle_connection_step(
        self,
        *,
        user_input: dict[str, str | int | float] | None,
        connection_type: str,
        data_schema: vol.Schema,
    ) -> FlowResult:
        """Validate and create the selected connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {CONF_CONNECTION_TYPE: connection_type, **user_input}
            unique_value = (
                str(user_input[CONF_DEVICE])
                if connection_type == CONNECTION_SERIAL
                else str(user_input[CONF_MQTT_STATE_TOPIC])
            )

            await self.async_set_unique_id(f"{connection_type}:{unique_value}")
            self._abort_if_unique_id_configured()

            client = ET312Client(
                ET312ConnectionConfig.from_mapping(data),
                hass=self.hass,
            )

            try:
                await client.async_validate_connection()
            except ET312ConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=self._build_title(connection_type, user_input),
                    data=data,
                )
            finally:
                await client.async_disconnect()

        return self.async_show_form(
            step_id=connection_type,
            data_schema=data_schema,
            errors=errors,
        )

    def _build_title(
        self,
        connection_type: str,
        user_input: dict[str, str | int | float],
    ) -> str:
        """Build a config-entry title."""
        if connection_type == CONNECTION_SERIAL:
            return f"{DEFAULT_NAME} {user_input[CONF_DEVICE]}"
        return f"{DEFAULT_NAME} MQTT {user_input[CONF_MQTT_STATE_TOPIC]}"
