"""Tests for Raspberry Pi ET312 multi-device helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.et312_rpi_manager import (
    bridge_topic_defaults,
    device_config_path,
    device_id_from_mac,
    next_rfcomm_device,
    parse_env_file,
    parse_patterns,
    register_bluetooth_device,
)


class RpiManagerTests(unittest.TestCase):
    """Exercise multi-device registry helpers without a Pi."""

    def test_device_id_uses_last_six_mac_characters(self) -> None:
        """Bluetooth ET312 ids should be stable and compact."""
        self.assertEqual(device_id_from_mac("A9:92:75:FE:12:DE"), "ET312_FE12DE")

    def test_bridge_topics_are_namespaced_by_device_id(self) -> None:
        """Each registered ET312 should get its own topic subtree."""
        self.assertEqual(
            bridge_topic_defaults("ET312_FE12DE", "et312"),
            {
                "MQTT_STATE_TOPIC": "et312/ET312_FE12DE/state",
                "MQTT_COMMAND_TOPIC": "et312/ET312_FE12DE/command",
                "MQTT_AVAILABILITY_TOPIC": "et312/ET312_FE12DE/availability",
            },
        )

    def test_next_rfcomm_device_skips_assigned_slots(self) -> None:
        """RFCOMM allocation should avoid clashes with already-known devices."""
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = Path(tmpdir)
            devices_dir = install_dir / "config" / "devices"
            devices_dir.mkdir(parents=True)
            (devices_dir / "ET312_AAAAAA.env").write_text(
                'DEVICE_ID="ET312_AAAAAA"\nRFCOMM_DEVICE="/dev/rfcomm0"\n',
                encoding="utf-8",
            )
            (devices_dir / "ET312_BBBBBB.env").write_text(
                'DEVICE_ID="ET312_BBBBBB"\nRFCOMM_DEVICE="/dev/rfcomm2"\n',
                encoding="utf-8",
            )

            self.assertEqual(next_rfcomm_device(install_dir), "/dev/rfcomm1")

    def test_register_bluetooth_device_preserves_custom_topics(self) -> None:
        """Re-registering a device should not clobber custom MQTT topics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = Path(tmpdir)
            bridge_config = install_dir / "config" / "et312-bridge.env"
            bridge_config.parent.mkdir(parents=True)
            bridge_config.write_text(
                'MQTT_HOST="127.0.0.1"\nMQTT_PORT="1883"\nMQTT_TOPIC_PREFIX="et312"\n',
                encoding="utf-8",
            )

            device_id = register_bluetooth_device(
                install_dir,
                mac="A9:92:75:FE:12:DE",
                rfcomm_device="/dev/rfcomm0",
                rfcomm_channel="2",
                bluetooth_name="Micro312 - Audio",
                device_id=None,
            )
            config_path = device_config_path(install_dir, device_id)
            initial = parse_env_file(config_path)
            initial["MQTT_STATE_TOPIC"] = "custom/one/state"
            config_path.write_text(
                "".join(f'{key}="{value}"\n' for key, value in sorted(initial.items())),
                encoding="utf-8",
            )

            register_bluetooth_device(
                install_dir,
                mac="A9:92:75:FE:12:DE",
                rfcomm_device="/dev/rfcomm0",
                rfcomm_channel="2",
                bluetooth_name="Micro312 - Audio",
                device_id=None,
            )

            self.assertEqual(parse_env_file(config_path)["MQTT_STATE_TOPIC"], "custom/one/state")

    def test_parse_patterns_drops_empty_entries(self) -> None:
        """Discovery name patterns should tolerate commas and spacing."""
        self.assertEqual(parse_patterns("Micro, 312, ,Audio"), ("Micro", "312", "Audio"))
