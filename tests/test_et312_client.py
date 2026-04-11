"""Tests for the ET312 client protocol helpers."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from custom_components.et312.const import (
    CONF_CONNECTION_TYPE,
    CONF_DEVICE_ID,
    CONF_MQTT_STATE_TOPIC,
    CONNECTION_SERIAL,
    CONNECTION_MQTT,
    CONTROL_FLAG_DISABLE_KNOBS,
    MODES,
    REG_CHANNEL_A_LEVEL,
    REG_CHANNEL_B_LEVEL,
    REG_BATTERY_PERCENT,
    REG_CONTROL_FLAGS,
    REG_CURRENT_MODE,
    REG_MULTI_ADJUST_RANGE_MAX,
    REG_MULTI_ADJUST_RANGE_MIN,
    REG_MULTI_ADJUST_VALUE,
    ROUTINES,
)
from custom_components.et312.et312 import (
    ET312Client,
    ET312ConnectionConfig,
    ET312ConnectionError,
    ET312TimeoutError,
    build_cipher_mask,
    flip_nibbles,
    raw_level_byte_to_ui_99,
    raw_multi_adjust_to_ui_percent,
    ui_99_to_raw_byte,
    ui_multi_adjust_to_raw_byte,
)
from custom_components.et312.topics import (
    build_topics,
    entry_device_id,
    extract_device_id_from_state_topic,
    extract_prefix_from_state_topic,
    is_valid_device_id,
    resolve_bridge_device_id,
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

    def test_live_level_scale_matches_front_panel_truncation(self) -> None:
        """Live A/B level registers should truncate the 0-255 byte to 0-99 like the ET312 UI."""
        self.assertEqual(raw_level_byte_to_ui_99(0x00), 0)
        self.assertEqual(raw_level_byte_to_ui_99(0x1C), 10)
        self.assertEqual(raw_level_byte_to_ui_99(0xFF), 99)

    def test_channel_power_scale_round_trips_to_display_value(self) -> None:
        """Channel writes should read back as the requested ET312 UI value."""
        for value in range(100):
            self.assertEqual(raw_level_byte_to_ui_99(ui_99_to_raw_byte(value)), value)

    def test_multi_adjust_scale_uses_documented_raw_range(self) -> None:
        """MA should map the current raw range into a 0-100 percentage."""
        self.assertEqual(raw_multi_adjust_to_ui_percent(0x00), 0)
        self.assertEqual(raw_multi_adjust_to_ui_percent(0x0F), 0)
        self.assertEqual(raw_multi_adjust_to_ui_percent(0x87), 50)
        self.assertEqual(raw_multi_adjust_to_ui_percent(0xFF), 100)
        self.assertEqual(raw_multi_adjust_to_ui_percent(0x20, 0x20, 0x60), 0)
        self.assertEqual(raw_multi_adjust_to_ui_percent(0x40, 0x20, 0x60), 50)
        self.assertEqual(raw_multi_adjust_to_ui_percent(0x60, 0x20, 0x60), 100)
        self.assertEqual(ui_multi_adjust_to_raw_byte(0), 0x0F)
        self.assertEqual(ui_multi_adjust_to_raw_byte(50), 0x87)
        self.assertEqual(ui_multi_adjust_to_raw_byte(100), 0xFF)
        self.assertEqual(ui_multi_adjust_to_raw_byte(0, 0x20, 0x60), 0x20)
        self.assertEqual(ui_multi_adjust_to_raw_byte(50, 0x20, 0x60), 0x40)
        self.assertEqual(ui_multi_adjust_to_raw_byte(100, 0x20, 0x60), 0x60)

    def test_flip_nibbles(self) -> None:
        """The ET312 host key uses nibble-flipping before XOR mask derivation."""
        self.assertEqual(flip_nibbles(0x00), 0x00)
        self.assertEqual(flip_nibbles(0xAB), 0xBA)
        self.assertEqual(flip_nibbles(0xF1), 0x1F)

    def test_build_cipher_mask(self) -> None:
        """The outbound cipher mask should match the documented ET312 formula."""
        self.assertEqual(build_cipher_mask(0x00, 0x00), 0x55)
        self.assertEqual(build_cipher_mask(0x12, 0x34), 0x40)

    def test_routines_exclude_power_presets(self) -> None:
        """Routine options should only include runnable programs, not box power presets."""
        self.assertNotIn("Power On", ROUTINES.values())
        self.assertNotIn("Low", ROUTINES.values())
        self.assertNotIn("Normal", ROUTINES.values())
        self.assertNotIn("High", ROUTINES.values())
        self.assertIn("Waves", ROUTINES.values())
        self.assertEqual(MODES[0x6C], "Normal")

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

    async def test_set_channel_power_writes_live_level_registers(self) -> None:
        """Channel power writes should enable software control and poke the live level bytes."""
        client = self._make_client()
        client.async_read_register = AsyncMock(return_value=0x00)
        client.async_write_register = AsyncMock()

        await client.async_set_channel_power("a", 99)
        await client.async_set_channel_power("b", 0)

        self.assertEqual(
            client.async_read_register.await_args_list,
            [unittest.mock.call(REG_CONTROL_FLAGS)],
        )
        self.assertEqual(
            client.async_write_register.await_args_list,
            [
                unittest.mock.call(REG_CONTROL_FLAGS, [CONTROL_FLAG_DISABLE_KNOBS]),
                unittest.mock.call(REG_CHANNEL_A_LEVEL, [ui_99_to_raw_byte(99)]),
                unittest.mock.call(REG_CHANNEL_B_LEVEL, [ui_99_to_raw_byte(0)]),
            ],
        )

    async def test_get_state_scales_raw_channel_power(self) -> None:
        """Polled state should expose the same 0-99 values the physical UI shows."""
        client = self._make_client()
        client.async_read_registers = AsyncMock(
            return_value={
                REG_CURRENT_MODE: 0x76,
                REG_CHANNEL_A_LEVEL: 0x1C,
                REG_CHANNEL_B_LEVEL: 0xFF,
                0x4203: 87,
                0x420D: 0x87,
                REG_CONTROL_FLAGS: CONTROL_FLAG_DISABLE_KNOBS,
            }
        )
        client.async_read_register = AsyncMock(side_effect=[0x0F, 0xFF])

        state = await client.async_get_state()

        self.assertEqual(state.mode, "Waves")
        self.assertEqual(state.power_level_a, 10)
        self.assertEqual(state.power_level_b, 99)
        self.assertEqual(state.battery_percent, 34)
        self.assertEqual(state.multi_adjust, 50)
        self.assertTrue(state.front_panel_controls_disabled)
        self.assertEqual(
            client.async_read_register.await_args_list,
            [
                unittest.mock.call(REG_MULTI_ADJUST_RANGE_MIN),
                unittest.mock.call(REG_MULTI_ADJUST_RANGE_MAX),
            ],
        )

    async def test_multi_adjust_bounds_refresh_only_when_mode_changes(self) -> None:
        """MA bounds should be cached until the ET312 reports a different mode."""
        client = self._make_client()
        state_payload = {
            REG_CHANNEL_A_LEVEL: 0x1C,
            REG_CHANNEL_B_LEVEL: 0xFF,
            REG_BATTERY_PERCENT: 87,
            REG_MULTI_ADJUST_VALUE: 0x40,
            REG_CONTROL_FLAGS: CONTROL_FLAG_DISABLE_KNOBS,
        }
        client.async_read_registers = AsyncMock(
            side_effect=[
                {REG_CURRENT_MODE: 0x76, **state_payload},
                {REG_CURRENT_MODE: 0x76, **state_payload},
                {REG_CURRENT_MODE: 0x77, **state_payload},
            ]
        )
        client.async_read_register = AsyncMock(
            side_effect=[0x20, 0x60, 0x10, 0x70]
        )

        first_state = await client.async_get_state()
        second_state = await client.async_get_state()
        third_state = await client.async_get_state()

        self.assertEqual(first_state.multi_adjust, 50)
        self.assertEqual(second_state.multi_adjust, 50)
        self.assertEqual(third_state.multi_adjust, 50)
        self.assertEqual(
            client.async_read_register.await_args_list,
            [
                unittest.mock.call(REG_MULTI_ADJUST_RANGE_MIN),
                unittest.mock.call(REG_MULTI_ADJUST_RANGE_MAX),
                unittest.mock.call(REG_MULTI_ADJUST_RANGE_MIN),
                unittest.mock.call(REG_MULTI_ADJUST_RANGE_MAX),
            ],
        )

    async def test_set_multi_adjust_writes_expected_register(self) -> None:
        """Multi-adjust writes should enable software control and poke the live MA register."""
        client = self._make_client()
        client.async_read_register = AsyncMock(side_effect=[0x20, 0x60, 0x00])
        client.async_write_register = AsyncMock()

        await client.async_set_multi_adjust(50)

        self.assertEqual(
            client.async_read_register.await_args_list,
            [
                unittest.mock.call(REG_MULTI_ADJUST_RANGE_MIN),
                unittest.mock.call(REG_MULTI_ADJUST_RANGE_MAX),
                unittest.mock.call(REG_CONTROL_FLAGS),
            ],
        )
        self.assertEqual(
            client.async_write_register.await_args_list,
            [
                unittest.mock.call(REG_CONTROL_FLAGS, [CONTROL_FLAG_DISABLE_KNOBS]),
                unittest.mock.call(
                    REG_MULTI_ADJUST_VALUE,
                    [ui_multi_adjust_to_raw_byte(50, 0x20, 0x60)],
                ),
            ],
        )

    async def test_set_front_panel_controls_disabled_updates_flag(self) -> None:
        """The front-panel control switch should set the disable-knobs bit."""
        client = self._make_client()
        client.async_read_register = AsyncMock(return_value=0x04)
        client.async_write_register = AsyncMock()

        await client.async_set_front_panel_controls_disabled(True)

        self.assertEqual(
            client.async_write_register.await_args_list,
            [unittest.mock.call(REG_CONTROL_FLAGS, [0x04 | CONTROL_FLAG_DISABLE_KNOBS])],
        )

    async def test_set_front_panel_controls_enabled_clears_flag(self) -> None:
        """Re-enabling front-panel controls should only clear the disable-knobs bit."""
        client = self._make_client()
        client.async_read_register = AsyncMock(return_value=0x0D)
        client.async_write_register = AsyncMock()

        await client.async_set_front_panel_controls_disabled(False)

        self.assertEqual(
            client.async_write_register.await_args_list,
            [unittest.mock.call(REG_CONTROL_FLAGS, [0x0C])],
        )

    async def test_invalid_channel_power_is_rejected(self) -> None:
        """Out-of-range UI values should fail before any write is attempted."""
        client = self._make_client()

        with self.assertRaises(ET312ConnectionError):
            await client.async_set_channel_power("a", 100)

    async def test_invalid_multi_adjust_is_rejected(self) -> None:
        """Out-of-range MA values should fail before any write is attempted."""
        client = self._make_client()

        with self.assertRaises(ET312ConnectionError):
            await client.async_set_multi_adjust(101)

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

    def test_topic_building_for_per_device_paths(self) -> None:
        """MQTT paths should be generated as prefix/device_id/<kind>."""
        self.assertEqual(
            build_topics("ET312_8EE738", "et312"),
            {
                "state": "et312/ET312_8EE738/state",
                "command": "et312/ET312_8EE738/command",
                "availability": "et312/ET312_8EE738/availability",
            },
        )

    def test_extract_device_id_and_prefix_from_state_topic(self) -> None:
        """Topic parsing should recover both the ET312 id and prefix."""
        topic = "et312/ET312_7D4FFB/state"
        self.assertEqual(extract_device_id_from_state_topic(topic), "ET312_7D4FFB")
        self.assertEqual(extract_prefix_from_state_topic(topic), "et312")
        self.assertIsNone(extract_device_id_from_state_topic("et312/state"))

    def test_device_id_validation(self) -> None:
        """Device ids should follow ET312_XXXXXX with hex suffix."""
        self.assertTrue(is_valid_device_id("et312_8ee738"))
        self.assertFalse(is_valid_device_id("ET312_12345"))
        self.assertFalse(is_valid_device_id("ET312_GG1234"))

    def test_bridge_device_id_resolves_from_config_or_topic(self) -> None:
        """Bridge payload ids should prefer config and otherwise use the topic path."""
        self.assertEqual(
            resolve_bridge_device_id("et312_8ee738", "et312/ET312_7D4FFB/state"),
            "ET312_8EE738",
        )
        self.assertEqual(
            resolve_bridge_device_id("", "et312/ET312_7D4FFB/state"),
            "ET312_7D4FFB",
        )
        self.assertEqual(resolve_bridge_device_id("", "et312/state"), "")

    def test_entry_device_id_prefers_explicit_and_falls_back_to_state_topic(self) -> None:
        """Coordinator ids should be stable for MQTT entries."""
        self.assertEqual(
            entry_device_id(
                {
                    CONF_CONNECTION_TYPE: CONNECTION_MQTT,
                    CONF_DEVICE_ID: "ET312_8EE738",
                    CONF_MQTT_STATE_TOPIC: "et312/ET312_7D4FFB/state",
                }
            ),
            "ET312_8EE738",
        )
        self.assertEqual(
            entry_device_id(
                {
                    CONF_CONNECTION_TYPE: CONNECTION_MQTT,
                    CONF_MQTT_STATE_TOPIC: "et312/ET312_7D4FFB/state",
                }
            ),
            "ET312_7D4FFB",
        )
