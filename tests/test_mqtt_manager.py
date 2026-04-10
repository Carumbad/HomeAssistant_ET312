"""Tests for ET312 MQTT discovery helpers."""

from __future__ import annotations

import unittest

from custom_components.et312.mqtt_payload import payload_to_text


class MqttManagerTests(unittest.TestCase):
    """Exercise MQTT discovery helpers without Home Assistant runtime."""

    def test_payload_to_text_decodes_bytes(self) -> None:
        """Availability payloads should compare as plain MQTT text."""
        self.assertEqual(payload_to_text(b"online"), "online")
        self.assertEqual(payload_to_text("offline"), "offline")


if __name__ == "__main__":
    unittest.main()
