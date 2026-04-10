"""Tests for Raspberry Pi ET312 multi-device helpers."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.et312_rpi_manager import (
    DEFAULT_ET312_RFCOMM_CHANNEL,
    bluetooth_alias_role,
    bridge_topic_defaults,
    choose_rfcomm_device,
    detect_rfcomm_channel,
    device_config_path,
    device_id_from_mac,
    next_rfcomm_device,
    parse_env_file,
    parse_patterns,
    register_bluetooth_device,
    split_bluetooth_aliases,
    update_devices_from_scan_line,
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

    def test_choose_rfcomm_device_avoids_duplicate_slots(self) -> None:
        """Re-registering a second ET312 should not reuse another device's RFCOMM slot."""
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = Path(tmpdir)
            devices_dir = install_dir / "config" / "devices"
            devices_dir.mkdir(parents=True)
            (devices_dir / "ET312_AAAAAA.env").write_text(
                'DEVICE_ID="ET312_AAAAAA"\nRFCOMM_DEVICE="/dev/rfcomm0"\n',
                encoding="utf-8",
            )

            self.assertEqual(
                choose_rfcomm_device(
                    install_dir,
                    preferred_device="/dev/rfcomm0",
                    device_id="ET312_BBBBBB",
                ),
                "/dev/rfcomm1",
            )

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
                pair_mac="AA:92:75:FE:12:DE",
                pair_name="Micro312 - SPP",
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
                pair_mac="AA:92:75:FE:12:DE",
                pair_name="Micro312 - SPP",
                device_id=None,
            )

            self.assertEqual(parse_env_file(config_path)["MQTT_STATE_TOPIC"], "custom/one/state")

    def test_register_bluetooth_device_resets_legacy_single_device_topics(self) -> None:
        """Legacy one-device MQTT topics should be replaced with per-device defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = Path(tmpdir)
            bridge_config = install_dir / "config" / "et312-bridge.env"
            bridge_config.parent.mkdir(parents=True)
            bridge_config.write_text(
                'MQTT_HOST="127.0.0.1"\nMQTT_PORT="1883"\nMQTT_TOPIC_PREFIX="et312"\n',
                encoding="utf-8",
            )
            config_path = device_config_path(install_dir, "ET312_FE12DE")
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                'DEVICE_ID="ET312_FE12DE"\n'
                'RFCOMM_DEVICE="/dev/rfcomm0"\n'
                'MQTT_STATE_TOPIC="et312/state"\n'
                'MQTT_COMMAND_TOPIC="et312/command"\n'
                'MQTT_AVAILABILITY_TOPIC="et312/availability"\n',
                encoding="utf-8",
            )

            register_bluetooth_device(
                install_dir,
                mac="A9:92:75:FE:12:DE",
                rfcomm_device="/dev/rfcomm0",
                rfcomm_channel="2",
                bluetooth_name="Micro312 - Audio",
                pair_mac="AA:92:75:FE:12:DE",
                pair_name="Micro312 - SPP",
                device_id="ET312_FE12DE",
            )

            rewritten = parse_env_file(config_path)
            self.assertEqual(rewritten["MQTT_STATE_TOPIC"], "et312/ET312_FE12DE/state")
            self.assertEqual(rewritten["MQTT_COMMAND_TOPIC"], "et312/ET312_FE12DE/command")
            self.assertEqual(
                rewritten["MQTT_AVAILABILITY_TOPIC"],
                "et312/ET312_FE12DE/availability",
            )

    def test_parse_patterns_drops_empty_entries(self) -> None:
        """Discovery name patterns should tolerate commas and spacing."""
        self.assertEqual(parse_patterns("Micro, 312, ,Audio"), ("Micro", "312", "Audio"))

    def test_bluetooth_alias_role_prefers_spp_and_audio(self) -> None:
        """Known ET312 alias names should be classified predictably."""
        self.assertEqual(bluetooth_alias_role("Micro312 - SPP"), "pair")
        self.assertEqual(bluetooth_alias_role("Micro312 - Audio"), "rfcomm")
        self.assertEqual(bluetooth_alias_role("Micro312"), "unknown")
        self.assertEqual(
            bluetooth_alias_role("Micro312 - Audio", "UUID: Public Key Open Credent.."),
            "pair",
        )

    def test_split_bluetooth_aliases_groups_pair_and_rfcomm_roles(self) -> None:
        """Discovery should pair the SPP alias and interrogate the Audio alias."""
        pair_candidates, rfcomm_candidates = split_bluetooth_aliases(
            [
                ("BF:B9:A5:7D:4F:FB", "Micro312 - SPP"),
                ("BE:B9:A5:7D:4F:FB", "Micro312 - Audio"),
            ]
        )
        self.assertEqual(pair_candidates, [("BF:B9:A5:7D:4F:FB", "Micro312 - SPP")])
        self.assertEqual(rfcomm_candidates, [("BE:B9:A5:7D:4F:FB", "Micro312 - Audio")])

    def test_split_bluetooth_aliases_uses_info_when_alias_name_is_misleading(self) -> None:
        """BlueZ alias names can drift, so UUID/class info should win."""
        pair_candidates, rfcomm_candidates = split_bluetooth_aliases(
            [
                ("BF:B9:A5:7D:4F:FB", "Micro312 - Audio"),
                ("BE:B9:A5:7D:4F:FB", "Micro312 - Audio"),
            ],
            {
                "BF:B9:A5:7D:4F:FB": "UUID: Public Key Open Credent.. (0000fff0-0000-1000-8000-00805f9b34fb)",
                "BE:B9:A5:7D:4F:FB": "UUID: Serial Port               (00001101-0000-1000-8000-00805f9b34fb)\nClass: 0x00340404",
            },
        )
        self.assertEqual(pair_candidates, [("BF:B9:A5:7D:4F:FB", "Micro312 - Audio")])
        self.assertEqual(rfcomm_candidates, [("BE:B9:A5:7D:4F:FB", "Micro312 - Audio")])

    def test_update_devices_from_scan_line_tracks_new_and_changed_names(self) -> None:
        """Live bluetoothctl output should surface both ET312 aliases."""
        devices: dict[str, str] = {}
        update_devices_from_scan_line(
            devices,
            "[NEW] Device BF:B9:A5:7D:4F:FB Micro312 - SPP",
        )
        update_devices_from_scan_line(
            devices,
            "[CHG] Device BE:B9:A5:7D:4F:FB Name: Micro312 - Audio",
        )
        update_devices_from_scan_line(
            devices,
            "[CHG] Device BE:B9:A5:7D:4F:FB Connected: yes",
        )
        self.assertEqual(
            devices,
            {
                "BF:B9:A5:7D:4F:FB": "Micro312 - SPP",
                "BE:B9:A5:7D:4F:FB": "Micro312 - Audio",
            },
        )

    def test_detect_rfcomm_channel_uses_et312_default_even_if_sdp_differs(self) -> None:
        """ET312 discovery should prefer the fixed channel 2 over SDP drift."""
        with patch("scripts.et312_rpi_manager.run_command") as run_command:
            run_command.return_value.stdout = "Channel: 5\n"
            self.assertEqual(
                detect_rfcomm_channel("BE:B9:A5:7D:4F:FB"),
                DEFAULT_ET312_RFCOMM_CHANNEL,
            )
