"""Tests for the ET312 client protocol helpers."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from custom_components.et312.const import (
    CHANNEL_A_BASE,
    CHANNEL_B_BASE,
    CHANNEL_POWER_MAX,
    CHANNEL_POWER_MIN,
    CONNECTION_SERIAL,
    REG_CHANNEL_A_LEVEL,
    REG_CHANNEL_B_LEVEL,
    REG_CURRENT_MODE,
)
from custom_components.et312.et312 import (
    ET312Client,
    ET312ConnectionConfig,
    ET312ConnectionError,
    ET312TimeoutError,
    build_cipher_mask,
    flip_nibbles,
    raw_power_to_ui,
    ui_power_to_raw,
)


class ET312ClientTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the ET312 client without real hardware."""

    def _make_client(self) -> ET312Client:
        client = ET312Client(
            ET312ConnectionConfig(
                connection_type=CONNECTION_SERIAL,
                device="/dev/ttyTEST",
                baudrate=19200,
                timeout=1.0,
            )
        )
        client.transport = AsyncMock()
        client._connected = True
        return client

    def test_power_scale_boundaries(self) -> None:
        """The displayed range should mirror the box's 0-99 UI."""
        self.assertEqual(raw_power_to_ui(CHANNEL_POWER_MIN), 0)
        self.assertEqual(raw_power_to_ui(CHANNEL_POWER_MAX), 99)
        self.assertEqual(ui_power_to_raw(0), CHANNEL_POWER_MIN)
        self.assertEqual(ui_power_to_raw(99), CHANNEL_POWER_MAX)

    def test_power_scale_round_trip_is_close(self) -> None:
        """UI power values should round-trip closely through the raw mapping."""
        for value in (0, 1, 25, 50, 75, 98, 99):
            self.assertLessEqual(abs(raw_power_to_ui(ui_power_to_raw(value)) - value), 1)

    def test_flip_nibbles(self) -> None:
        """The ET312 host key uses nibble-flipping before XOR mask derivation."""
        self.assertEqual(flip_nibbles(0x00), 0x00)
        self.assertEqual(flip_nibbles(0xAB), 0xBA)
        self.assertEqual(flip_nibbles(0xF1), 0x1F)

    def test_build_cipher_mask(self) -> None:
        """The outbound cipher mask should match the documented ET312 formula."""
        self.assertEqual(build_cipher_mask(0x00, 0x00), 0x55)
        self.assertEqual(build_cipher_mask(0x12, 0x34), 0x40)

    async def test_set_mode_writes_expected_command_sequence(self) -> None:
        """Mode changes should write the mode and then queue the ET312 commands."""
        client = self._make_client()
        client.async_write_register = AsyncMock()

        await client.async_set_mode("Waves")

        self.assertEqual(
            client.async_write_register.await_args_list,
            [
                unittest.mock.call(REG_CURRENT_MODE, [0x76]),
                unittest.mock.call(0x4070, [0x04, 0x12]),
            ],
        )

    async def test_set_channel_power_writes_expected_registers(self) -> None:
        """Channel power writes should clear modulation/select state before setting power."""
        client = self._make_client()
        client.async_write_register = AsyncMock()

        await client.async_set_channel_power("a", 99)
        await client.async_set_channel_power("b", 0)

        self.assertEqual(
            client.async_write_register.await_args_list,
            [
                unittest.mock.call(CHANNEL_A_BASE + 0xAC, [0x00]),
                unittest.mock.call(CHANNEL_A_BASE + 0xA8, [0x00, 0x00]),
                unittest.mock.call(CHANNEL_A_BASE + 0xA5, [CHANNEL_POWER_MAX]),
                unittest.mock.call(CHANNEL_B_BASE + 0xAC, [0x00]),
                unittest.mock.call(CHANNEL_B_BASE + 0xA8, [0x00, 0x00]),
                unittest.mock.call(CHANNEL_B_BASE + 0xA5, [CHANNEL_POWER_MIN]),
            ],
        )

    async def test_get_state_scales_raw_channel_power(self) -> None:
        """Polled state should expose the same 0-99 values the physical UI shows."""
        client = self._make_client()
        client.async_read_registers = AsyncMock(
            return_value={
                REG_CURRENT_MODE: 0x76,
                REG_CHANNEL_A_LEVEL: 0x00,
                REG_CHANNEL_B_LEVEL: 0xFF,
                0x4203: 87,
                0x420D: 55,
            }
        )

        state = await client.async_get_state()

        self.assertEqual(state.mode, "Waves")
        self.assertEqual(state.power_level_a, 0)
        self.assertEqual(state.power_level_b, 99)
        self.assertEqual(state.battery_percent, 34)
        self.assertEqual(state.multi_adjust, 21)

    async def test_invalid_channel_power_is_rejected(self) -> None:
        """Out-of-range UI values should fail before any write is attempted."""
        client = self._make_client()

        with self.assertRaises(ET312ConnectionError):
            await client.async_set_channel_power("a", 100)

    async def test_key_setup_timeout_falls_back_to_box_key_zero(self) -> None:
        """Bluetooth sessions should tolerate missing key exchange replies."""
        client = self._make_client()
        client.transport.async_write = AsyncMock()
        client.transport.async_flush_input = AsyncMock()
        client.transport.async_read = AsyncMock(
            side_effect=[
                ET312TimeoutError("first key exchange timeout"),
                b"\x07",
                ET312TimeoutError("second key exchange timeout"),
            ]
        )

        await client.async_setup_keys()

        self.assertEqual(client._box_key, 0x00)
        self.assertEqual(client._cipher_mask, build_cipher_mask(0x00, 0x00))
