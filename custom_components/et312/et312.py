"""ET312 client and protocol abstractions."""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

try:
    from homeassistant.util import slugify
except ImportError:
    def slugify(value: str) -> str:
        """Fallback slugify for standalone testing."""
        return "".join(
            char.lower() if char.isalnum() else "_"
            for char in value.strip()
        ).strip("_")

from .const import (
    CHANNEL_POWER_MAX,
    CHANNEL_POWER_MIN,
    CHANNEL_POWER_UI_MAX,
    CHANNEL_POWER_UI_MIN,
    MODES,
    MULTI_ADJUST_UI_MAX,
    MULTI_ADJUST_UI_MIN,
    CONF_BAUDRATE,
    CONF_CONNECTION_TYPE,
    CONF_DEVICE,
    CONF_MQTT_AVAILABILITY_TOPIC,
    CONF_MQTT_COMMAND_TOPIC,
    CONF_MQTT_STATE_TOPIC,
    CONF_TIMEOUT,
    CONNECTION_MQTT,
    CONNECTION_SERIAL,
    CONTROL_FLAG_DISABLE_KNOBS,
    REG_EXECUTE_COMMAND,
    REG_BATTERY_PERCENT,
    REG_CHANNEL_A_LEVEL,
    REG_CHANNEL_B_LEVEL,
    REG_CIPHER_KEY,
    REG_CONTROL_FLAGS,
    REG_CURRENT_MODE,
    REG_MULTI_ADJUST_VALUE,
    ROUTINES,
)


class ET312ConnectionError(Exception):
    """Raised when the ET312 cannot be reached or queried."""


class ET312TimeoutError(ET312ConnectionError):
    """Raised when the ET312 does not answer before the timeout expires."""


@dataclass(slots=True)
class ET312State:
    """Minimal normalized ET312 state."""

    connected: bool
    mode_code: int | None
    mode: str | None
    power_level_a: int | None
    power_level_b: int | None
    mode_options: tuple[str, ...]
    battery_percent: int | None
    multi_adjust: int | None
    front_panel_controls_disabled: bool

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ET312State":
        """Build ET312 state from an MQTT bridge payload."""
        modes = payload.get("mode_options") or payload.get("available_modes") or [
            ROUTINES[code] for code in sorted(ROUTINES)
        ]
        return cls(
            connected=bool(payload.get("connected", True)),
            mode_code=_optional_int(payload.get("mode_code")),
            mode=_optional_str(payload.get("mode")),
            power_level_a=_optional_int(payload.get("power_level_a")),
            power_level_b=_optional_int(payload.get("power_level_b")),
            mode_options=tuple(str(mode) for mode in modes),
            battery_percent=_optional_int(payload.get("battery_percent")),
            multi_adjust=_optional_int(payload.get("multi_adjust")),
            front_panel_controls_disabled=bool(
                payload.get("front_panel_controls_disabled", False)
            ),
        )


@dataclass(slots=True)
class ET312ConnectionConfig:
    """User-selected ET312 connection details."""

    connection_type: str
    timeout: float
    device: str | None = None
    baudrate: int | None = None
    mqtt_state_topic: str | None = None
    mqtt_command_topic: str | None = None
    mqtt_availability_topic: str | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, object]) -> "ET312ConnectionConfig":
        """Build a typed connection config from a config-entry mapping."""
        return cls(
            connection_type=str(data[CONF_CONNECTION_TYPE]),
            timeout=float(data[CONF_TIMEOUT]),
            device=_optional_str(data.get(CONF_DEVICE)),
            baudrate=_optional_int(data.get(CONF_BAUDRATE)),
            mqtt_state_topic=_optional_str(data.get(CONF_MQTT_STATE_TOPIC)),
            mqtt_command_topic=_optional_str(data.get(CONF_MQTT_COMMAND_TOPIC)),
            mqtt_availability_topic=_optional_str(data.get(CONF_MQTT_AVAILABILITY_TOPIC)),
        )


def _optional_str(value: object | None) -> str | None:
    """Convert an optional value to a string."""
    if value is None:
        return None
    return str(value)


def _optional_int(value: object | None) -> int | None:
    """Convert an optional value to an integer."""
    if value is None:
        return None
    return int(value)


def raw_power_to_ui(raw_value: int) -> int:
    """Convert an ET312 raw power register value to the displayed 0-99 scale."""
    clamped = min(max(raw_value, CHANNEL_POWER_MIN), CHANNEL_POWER_MAX)
    return round(
        ((clamped - CHANNEL_POWER_MIN) * CHANNEL_POWER_UI_MAX)
        / (CHANNEL_POWER_MAX - CHANNEL_POWER_MIN)
    )


def ui_power_to_raw(ui_value: int) -> int:
    """Convert a displayed 0-99 power level to the ET312 raw register scale."""
    clamped = min(max(ui_value, CHANNEL_POWER_UI_MIN), CHANNEL_POWER_UI_MAX)
    return CHANNEL_POWER_MIN + round(
        (clamped * (CHANNEL_POWER_MAX - CHANNEL_POWER_MIN))
        / CHANNEL_POWER_UI_MAX
    )


def raw_byte_to_ui_99(raw_value: int) -> int:
    """Convert a generic ET312 raw byte to the device's 0-99 UI scale."""
    clamped = min(max(raw_value, 0), 0xFF)
    return round((clamped * CHANNEL_POWER_UI_MAX) / 0xFF)


def raw_multi_adjust_to_ui_99(raw_value: int) -> int:
    """Convert the ET312 multi-adjust byte to the front-panel 0-99 scale.

    The ET312 stores MA inverted: a higher front-panel value maps to a lower
    raw byte value.
    """
    return CHANNEL_POWER_UI_MAX - raw_byte_to_ui_99(raw_value)


def raw_level_byte_to_ui_99(raw_value: int) -> int:
    """Convert a live ET312 level byte to the truncated 0-99 front-panel scale."""
    clamped = min(max(raw_value, 0), 0xFF)
    return (clamped * CHANNEL_POWER_UI_MAX) // 0xFF


def ui_99_to_raw_byte(ui_value: int) -> int:
    """Convert a 0-99 UI value to a generic ET312 raw byte."""
    clamped = min(max(ui_value, MULTI_ADJUST_UI_MIN), MULTI_ADJUST_UI_MAX)
    return round((clamped * 0xFF) / MULTI_ADJUST_UI_MAX)


def ui_multi_adjust_to_raw_byte(ui_value: int) -> int:
    """Convert a front-panel 0-99 MA value to the ET312's inverted raw byte."""
    return ui_99_to_raw_byte(CHANNEL_POWER_UI_MAX - ui_value)


def calculate_checksum(data: list[int]) -> int:
    """Calculate the ET312 packet checksum."""
    return sum(data) & 0xFF


def flip_nibbles(value: int) -> int:
    """Flip the high/low nibbles in an ET312 key byte."""
    return ((value & 0x0F) << 4) | ((value >> 4) & 0x0F)


def build_cipher_mask(host_key: int, box_key: int) -> int:
    """Build the ET312 outbound XOR mask for encrypted host-to-device bytes."""
    return flip_nibbles(host_key) ^ box_key ^ 0x55


def apply_cipher(data: list[int], mask: int | None) -> list[int]:
    """Apply the ET312 outbound XOR mask to packet bytes."""
    if mask is None:
        return list(data)
    return [byte ^ mask for byte in data]


def build_read_command(address: int) -> list[int]:
    """Build a read-memory command for a single register."""
    packet = [0x3C, (address >> 8) & 0xFF, address & 0xFF]
    return packet + [calculate_checksum(packet)]


def build_write_command(address: int, values: list[int]) -> list[int]:
    """Build a write-memory command for up to 8 bytes."""
    if not values or len(values) > 8:
        raise ValueError("ET312 writes must contain between 1 and 8 bytes")
    packet = [0x3D + (len(values) << 4), (address >> 8) & 0xFF, address & 0xFF, *values]
    return packet + [calculate_checksum(packet)]


def decode_read_response(data: list[int]) -> int:
    """Decode a read response and return the register value."""
    if len(data) != 3:
        raise ET312ConnectionError(f"Unexpected ET312 read response length: {len(data)}")
    if calculate_checksum(data[:-1]) != data[-1]:
        raise ET312ConnectionError("ET312 read response checksum mismatch")
    if data[0] != 0x22:
        raise ET312ConnectionError(f"Unexpected ET312 read response opcode: 0x{data[0]:02X}")
    return data[1]


def decode_write_response(data: list[int]) -> None:
    """Validate a write response."""
    if len(data) != 1:
        raise ET312ConnectionError(f"Unexpected ET312 write response length: {len(data)}")
    if data[0] == 0x07:
        raise ET312ConnectionError("ET312 rejected the write, likely due to sync or key state")
    if data[0] != 0x06:
        raise ET312ConnectionError(f"Unexpected ET312 write response opcode: 0x{data[0]:02X}")


class ET312Transport(ABC):
    """Abstract byte transport for ET312 communications."""

    @abstractmethod
    async def async_open(self) -> None:
        """Open the underlying connection."""

    @abstractmethod
    async def async_close(self) -> None:
        """Close the underlying connection."""

    @abstractmethod
    async def async_write(self, data: bytes) -> None:
        """Write raw bytes."""

    @abstractmethod
    async def async_read(self, length: int, timeout: float | None = None) -> bytes:
        """Read raw bytes."""

    async def async_flush_input(self) -> None:
        """Discard buffered input if the transport supports it."""


class PlaceholderTransport(ET312Transport):
    """Temporary transport until the real serial backend is implemented."""

    def __init__(self, *, device: str, baudrate: int, timeout: float) -> None:
        self.device = device
        self.baudrate = baudrate
        self.timeout = timeout

    async def async_open(self) -> None:
        raise ET312ConnectionError(
            "Serial transport is not implemented yet. "
            "The next step is to wire this client to pyserial-asyncio or HA serial helpers."
        )

    async def async_close(self) -> None:
        return None

    async def async_write(self, data: bytes) -> None:
        raise ET312ConnectionError("Serial transport is not implemented yet")

    async def async_read(self, length: int, timeout: float | None = None) -> bytes:
        raise ET312ConnectionError("Serial transport is not implemented yet")


class SerialTransport(ET312Transport):
    """Serial transport for a directly connected ET312."""

    def __init__(self, *, device: str, baudrate: int, timeout: float) -> None:
        self.device = device
        self.baudrate = baudrate
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def async_open(self) -> None:
        """Open the serial connection."""
        if self._reader is not None and self._writer is not None:
            return

        try:
            import serial
        except ImportError as err:
            raise ET312ConnectionError(
                "Serial dependencies are missing; install pyserial and "
                "pyserial-asyncio-fast or pyserial-asyncio."
            ) from err

        serial_asyncio_module = None
        try:
            import serial_asyncio_fast as serial_asyncio_module
        except ImportError:
            try:
                import serial_asyncio as serial_asyncio_module
            except ImportError as err:
                raise ET312ConnectionError(
                    "Serial async dependency is missing; install "
                    "pyserial-asyncio-fast or pyserial-asyncio."
                ) from err

        try:
            reader, writer = await serial_asyncio_module.open_serial_connection(
                url=self.device,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
        except Exception as err:
            raise ET312ConnectionError(
                f"Unable to open ET312 serial device {self.device}: {err}"
            ) from err

        self._reader = reader
        self._writer = writer

    async def async_close(self) -> None:
        """Close the serial connection."""
        writer = self._writer
        self._reader = None
        self._writer = None

        if writer is None:
            return

        writer.close()
        wait_closed = getattr(writer, "wait_closed", None)
        if wait_closed is not None:
            await wait_closed()

    async def async_write(self, data: bytes) -> None:
        """Write bytes to the serial connection."""
        if self._writer is None:
            raise ET312ConnectionError("ET312 serial device is not open")

        self._writer.write(data)
        await self._writer.drain()

    async def async_read(self, length: int, timeout: float | None = None) -> bytes:
        """Read an exact number of bytes from the serial connection."""
        if self._reader is None:
            raise ET312ConnectionError("ET312 serial device is not open")

        read_coro = self._reader.readexactly(length)

        try:
            if timeout is None:
                return await read_coro
            return await asyncio.wait_for(read_coro, timeout=timeout)
        except asyncio.TimeoutError as err:
            raise ET312TimeoutError(
                f"Timed out waiting for {length} byte(s) from the ET312"
            ) from err
        except asyncio.IncompleteReadError as err:
            raise ET312ConnectionError(
                f"ET312 disconnected while reading {length} byte(s)"
            ) from err

    async def async_flush_input(self) -> None:
        """Drain any buffered input from the serial device."""
        while True:
            try:
                await self.async_read(1, timeout=0.05)
            except ET312TimeoutError:
                return


class MQTTBridgeTransport(ET312Transport):
    """MQTT transport for a remote ET312 bridge."""

    def __init__(
        self,
        *,
        hass,
        state_topic: str,
        command_topic: str,
        availability_topic: str | None,
        timeout: float,
    ) -> None:
        self.hass = hass
        self.state_topic = state_topic
        self.command_topic = command_topic
        self.availability_topic = availability_topic
        self.timeout = timeout
        self._state: ET312State | None = None
        self._opened = False
        self._state_event = asyncio.Event()
        self._unsubscribers: list[Any] = []

    async def async_open(self) -> None:
        """Subscribe to bridge topics and wait for the MQTT client."""
        if self._opened:
            return
        if self.hass is None:
            raise ET312ConnectionError("MQTT transport requires Home Assistant context")

        from homeassistant.components import mqtt
        from homeassistant.core import callback

        await mqtt.async_wait_for_mqtt_client(self.hass)

        @callback
        def _state_message_received(msg) -> None:
            payload = json.loads(msg.payload)
            self._state = ET312State.from_dict(payload)
            self._state_event.set()

        self._unsubscribers.append(
            await mqtt.async_subscribe(self.hass, self.state_topic, _state_message_received)
        )

        if self.availability_topic:
            @callback
            def _availability_message_received(msg) -> None:
                available = msg.payload.lower() == "online"
                if self._state is None:
                    self._state = ET312State(
                        connected=available,
                        mode_code=None,
                        mode=None,
                        power_level_a=None,
                        power_level_b=None,
                        mode_options=tuple(ROUTINES[code] for code in sorted(ROUTINES)),
                        battery_percent=None,
                        multi_adjust=None,
                        front_panel_controls_disabled=False,
                    )
                else:
                    self._state = ET312State(
                        connected=available,
                        mode_code=self._state.mode_code,
                        mode=self._state.mode,
                        power_level_a=self._state.power_level_a,
                        power_level_b=self._state.power_level_b,
                        mode_options=self._state.mode_options,
                        battery_percent=self._state.battery_percent,
                        multi_adjust=self._state.multi_adjust,
                        front_panel_controls_disabled=self._state.front_panel_controls_disabled,
                    )
                self._state_event.set()

            self._unsubscribers.append(
                await mqtt.async_subscribe(
                    self.hass, self.availability_topic, _availability_message_received
                )
            )

        self._opened = True
        await self.async_publish_command({"command": "request_state"})

    async def async_close(self) -> None:
        """Unsubscribe from bridge topics."""
        while self._unsubscribers:
            unsubscribe = self._unsubscribers.pop()
            unsubscribe()
        self._opened = False

    async def async_write(self, data: bytes) -> None:
        raise ET312ConnectionError("Raw byte writes are not supported over MQTT")

    async def async_read(self, length: int, timeout: float | None = None) -> bytes:
        raise ET312ConnectionError("Raw byte reads are not supported over MQTT")

    async def async_publish_command(self, payload: dict[str, Any]) -> None:
        """Publish a JSON bridge command."""
        if self.hass is None:
            raise ET312ConnectionError("MQTT transport requires Home Assistant context")
        from homeassistant.components import mqtt

        await mqtt.async_publish(
            self.hass,
            self.command_topic,
            json.dumps(payload),
            qos=0,
            retain=False,
        )

    async def async_get_state(self) -> ET312State:
        """Return the latest bridge state, waiting briefly if necessary."""
        if self._state is not None:
            return self._state

        self._state_event.clear()
        await self.async_publish_command({"command": "request_state"})
        try:
            await asyncio.wait_for(self._state_event.wait(), timeout=self.timeout)
        except asyncio.TimeoutError as err:
            raise ET312TimeoutError("Timed out waiting for ET312 MQTT state") from err

        if self._state is None:
            raise ET312ConnectionError("ET312 MQTT bridge did not publish state")
        return self._state


class ET312Client:
    """Async client wrapper for the ET312 protocol."""

    def __init__(self, config: ET312ConnectionConfig, hass=None) -> None:
        """Initialize the client."""
        self.config = config
        self.hass = hass
        self.timeout = config.timeout
        self.transport = self._build_transport(config)
        self._connected = False
        self._host_key = 0x00
        self._box_key: int | None = None
        self._cipher_mask: int | None = None
        self._last_cipher_mask: int | None = None
        self._current_control_flags: int | None = None

    def _build_transport(self, config: ET312ConnectionConfig) -> ET312Transport:
        """Create the selected ET312 transport."""
        if config.connection_type == CONNECTION_SERIAL:
            if not config.device or not config.baudrate:
                raise ET312ConnectionError("Serial ET312 config is missing device or baudrate")
            return SerialTransport(
                device=config.device,
                baudrate=config.baudrate,
                timeout=config.timeout,
            )

        if config.connection_type == CONNECTION_MQTT:
            if (
                not config.mqtt_state_topic
                or not config.mqtt_command_topic
            ):
                raise ET312ConnectionError("MQTT ET312 config is incomplete")
            return MQTTBridgeTransport(
                hass=self.hass,
                state_topic=config.mqtt_state_topic,
                command_topic=config.mqtt_command_topic,
                availability_topic=config.mqtt_availability_topic,
                timeout=config.timeout,
            )

        raise ET312ConnectionError(
            f"Unsupported ET312 connection type: {config.connection_type}"
        )

    async def async_validate_connection(self) -> None:
        """Validate connectivity to the device."""
        await self.async_connect()
        if self.config.connection_type == CONNECTION_MQTT:
            await self.transport.async_get_state()

    async def async_connect(self) -> None:
        """Open the transport and negotiate the ET312 protocol if needed."""
        if self._connected:
            return

        await self.transport.async_open()

        try:
            if self.config.connection_type == CONNECTION_SERIAL:
                await asyncio.sleep(0.2)
                await self.transport.async_flush_input()
                await self._async_sync_with_retries()
                await self.async_setup_keys()
        except Exception:
            await self.transport.async_close()
            self._connected = False
            self._box_key = None
            self._cipher_mask = None
            self._current_control_flags = None
            raise

        self._connected = True

    async def async_get_state(self) -> ET312State:
        """Return the latest device state."""
        if not self._connected:
            await self.async_connect()

        if self.config.connection_type == CONNECTION_MQTT:
            return await self.transport.async_get_state()

        registers = await self.async_read_registers(
            [
                REG_CURRENT_MODE,
                REG_CHANNEL_A_LEVEL,
                REG_CHANNEL_B_LEVEL,
                REG_BATTERY_PERCENT,
                REG_MULTI_ADJUST_VALUE,
                REG_CONTROL_FLAGS,
            ]
        )

        mode_code = registers[REG_CURRENT_MODE]
        self._current_control_flags = registers[REG_CONTROL_FLAGS]
        return ET312State(
            connected=True,
            mode_code=mode_code,
            mode=MODES.get(mode_code, f"Unknown (0x{mode_code:02X})"),
            power_level_a=raw_level_byte_to_ui_99(registers[REG_CHANNEL_A_LEVEL]),
            power_level_b=raw_level_byte_to_ui_99(registers[REG_CHANNEL_B_LEVEL]),
            mode_options=tuple(ROUTINES[code] for code in sorted(ROUTINES)),
            battery_percent=raw_byte_to_ui_99(registers[REG_BATTERY_PERCENT]),
            multi_adjust=raw_multi_adjust_to_ui_99(registers[REG_MULTI_ADJUST_VALUE]),
            front_panel_controls_disabled=bool(
                registers[REG_CONTROL_FLAGS] & CONTROL_FLAG_DISABLE_KNOBS
            ),
        )

    async def async_disconnect(self) -> None:
        """Disconnect from the device."""
        if self._connected and self.config.connection_type == CONNECTION_SERIAL:
            try:
                await self.async_reset_key()
            except ET312ConnectionError:
                pass
        await self.transport.async_close()
        self._connected = False
        self._box_key = None
        self._cipher_mask = None
        self._current_control_flags = None

    async def async_read_register(self, address: int) -> int:
        """Read a single ET312 register."""
        payload = build_read_command(address)
        await self.transport.async_write(bytes(apply_cipher(payload, self._cipher_mask)))
        response = list(await self.transport.async_read(3, timeout=self.timeout))
        return decode_read_response(response)

    async def async_write_register(self, address: int, values: list[int]) -> None:
        """Write one or more bytes to an ET312 register."""
        payload = build_write_command(address, values)
        await self.transport.async_write(bytes(apply_cipher(payload, self._cipher_mask)))
        response = list(await self.transport.async_read(1, timeout=self.timeout))
        decode_write_response(response)

    async def async_read_registers(self, addresses: list[int]) -> dict[int, int]:
        """Read a batch of registers sequentially."""
        result: dict[int, int] = {}
        for address in addresses:
            result[address] = await self.async_read_register(address)
        return result

    async def _async_get_control_flags(self) -> int:
        """Return the current ET312 control flags."""
        if self._current_control_flags is not None:
            return self._current_control_flags

        flags = await self.async_read_register(REG_CONTROL_FLAGS)
        self._current_control_flags = flags
        return flags

    async def _async_set_control_flags(self, desired_flags: int) -> None:
        """Write ET312 control flags when the value changes."""
        current_flags = await self._async_get_control_flags()
        if desired_flags == current_flags:
            return

        await self.async_write_register(REG_CONTROL_FLAGS, [desired_flags])
        self._current_control_flags = desired_flags

    async def async_set_front_panel_controls_disabled(self, disabled: bool) -> None:
        """Enable or disable the ET312 front-panel knobs."""
        if self.config.connection_type == CONNECTION_MQTT:
            await self.transport.async_publish_command(
                {"command": "set_front_panel_controls_disabled", "value": disabled}
            )
            return

        current_flags = await self._async_get_control_flags()
        if disabled:
            desired_flags = current_flags | CONTROL_FLAG_DISABLE_KNOBS
        else:
            desired_flags = current_flags & ~CONTROL_FLAG_DISABLE_KNOBS

        await self._async_set_control_flags(desired_flags)

    async def async_reset_key(self) -> None:
        """Reset the device cipher key if we negotiated one."""
        if self._cipher_mask is None:
            return
        await self.async_write_register(REG_CIPHER_KEY, [0x00])
        self._box_key = None
        self._cipher_mask = None

    async def async_set_mode(self, mode_name: str) -> None:
        """Switch the ET312 to a new routine/mode."""
        if self.config.connection_type == CONNECTION_MQTT:
            self._mode_code_from_name(mode_name)
            await self.transport.async_publish_command(
                {"command": "set_mode", "mode": mode_name}
            )
            return

        mode_code = self._mode_code_from_name(mode_name)
        await self.async_write_register(REG_CURRENT_MODE, [mode_code])
        await self.async_write_register(REG_EXECUTE_COMMAND, [0x04, 0x12])
        await asyncio.sleep(0.02)

    async def async_set_channel_power(self, channel: str, level: int) -> None:
        """Set the current output level for a channel."""
        if level < CHANNEL_POWER_UI_MIN or level > CHANNEL_POWER_UI_MAX:
            raise ET312ConnectionError(
                f"Channel power must be between {CHANNEL_POWER_UI_MIN} and {CHANNEL_POWER_UI_MAX}"
            )
        if self.config.connection_type == CONNECTION_MQTT:
            if channel not in {"a", "b"}:
                raise ET312ConnectionError(f"Unknown ET312 channel: {channel}")
            await self.transport.async_publish_command(
                {"command": "set_power", "channel": channel, "value": level}
            )
            return

        if channel == "a":
            level_register = REG_CHANNEL_A_LEVEL
        elif channel == "b":
            level_register = REG_CHANNEL_B_LEVEL
        else:
            raise ET312ConnectionError(f"Unknown ET312 channel: {channel}")

        current_flags = await self._async_get_control_flags()
        await self._async_set_control_flags(current_flags | CONTROL_FLAG_DISABLE_KNOBS)
        await self.async_write_register(level_register, [ui_99_to_raw_byte(level)])

    async def async_set_multi_adjust(self, value: int) -> None:
        """Set the ET312 multi-adjust value."""
        if value < MULTI_ADJUST_UI_MIN or value > MULTI_ADJUST_UI_MAX:
            raise ET312ConnectionError(
                "Multi Adjust must be between "
                f"{MULTI_ADJUST_UI_MIN} and {MULTI_ADJUST_UI_MAX}"
            )
        if self.config.connection_type == CONNECTION_MQTT:
            await self.transport.async_publish_command(
                {"command": "set_multi_adjust", "value": value}
            )
            return

        current_flags = await self._async_get_control_flags()
        await self._async_set_control_flags(current_flags | CONTROL_FLAG_DISABLE_KNOBS)
        await self.async_write_register(
            REG_MULTI_ADJUST_VALUE,
            [ui_multi_adjust_to_raw_byte(value)],
        )

    def _mode_code_from_name(self, mode_name: str) -> int:
        """Resolve a Home Assistant select option to an ET312 mode code."""
        normalized = slugify(mode_name)
        for code, name in ROUTINES.items():
            if slugify(name) == normalized:
                return code
        raise ET312ConnectionError(f"Unsupported ET312 mode: {mode_name}")

    async def async_sync(self) -> None:
        """Realign the ET312 packet stream."""
        for _ in range(12):
            await self.transport.async_write(bytes(apply_cipher([0x00], self._cipher_mask)))
            try:
                response = await self.transport.async_read(1, timeout=0.1)
            except ET312TimeoutError:
                continue

            if response[0] != 0x07:
                raise ET312ConnectionError(
                    f"Unexpected ET312 sync response: 0x{response[0]:02X}"
                )

            return

        raise ET312ConnectionError("ET312 synchronisation failed")

    async def _async_sync_with_retries(self) -> None:
        """Try a few sync strategies before giving up on the ET312 session."""
        errors: list[str] = []

        for candidate_mask in (
            None,
            self._last_cipher_mask,
            build_cipher_mask(self._host_key, 0x00),
        ):
            self._cipher_mask = candidate_mask
            try:
                await self.async_sync()
                return
            except ET312ConnectionError as err:
                errors.append(f"mask={candidate_mask!r}: {err}")
                await asyncio.sleep(0.2)
                await self.transport.async_flush_input()

        self._cipher_mask = None
        raise ET312ConnectionError(
            "ET312 synchronisation failed after retrying fallback key handling "
            f"({'; '.join(errors)})"
        )

    async def async_setup_keys(self) -> None:
        """Negotiate the ET312 outbound XOR key."""
        command = [0x2F, self._host_key]
        payload = command + [calculate_checksum(command)]

        try:
            await self.transport.async_write(bytes(apply_cipher(payload, self._cipher_mask)))
            response = list(await self.transport.async_read(3, timeout=1.0))
        except ET312TimeoutError:
            self._box_key = 0x00
            self._cipher_mask = build_cipher_mask(self._host_key, self._box_key)
            self._last_cipher_mask = self._cipher_mask
            return

        if calculate_checksum(response[:-1]) != response[-1]:
            raise ET312ConnectionError("ET312 key setup checksum mismatch")
        if response[0] != 0x21:
            raise ET312ConnectionError(
                f"Unexpected ET312 key setup response: 0x{response[0]:02X}"
            )

        self._box_key = response[1]
        self._cipher_mask = build_cipher_mask(self._host_key, self._box_key)
        self._last_cipher_mask = self._cipher_mask
