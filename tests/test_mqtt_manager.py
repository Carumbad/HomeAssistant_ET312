"""Tests for ET312 MQTT discovery helpers."""

from __future__ import annotations

import unittest

from custom_components.et312.mqtt_payload import (
    command_payload_for_device,
    payload_to_text,
)


class MqttManagerTests(unittest.TestCase):
    """Exercise MQTT discovery helpers without Home Assistant runtime."""

    def test_payload_to_text_decodes_bytes(self) -> None:
        """Availability payloads should compare as plain MQTT text."""
        self.assertEqual(payload_to_text(b"online"), "online")
        self.assertEqual(payload_to_text("offline"), "offline")

    def test_command_payload_for_device_adds_normalized_device_id(self) -> None:
        """MQTT commands should carry the same stable device id as state payloads."""
        self.assertEqual(
            command_payload_for_device(
                "et312_8ee738",
                {"command": "set_power", "device_id": "wrong"},
            ),
            {"command": "set_power", "device_id": "ET312_8EE738"},
        )


if __name__ == "__main__":
    unittest.main()
