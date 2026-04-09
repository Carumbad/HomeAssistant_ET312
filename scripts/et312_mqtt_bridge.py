"""Simple ET312-to-MQTT bridge."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import serial

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.et312.const import MODES
from custom_components.et312.et312 import (
    apply_cipher,
    build_read_command,
    build_write_command,
    calculate_checksum,
    decode_read_response,
    raw_byte_to_ui_99,
    ui_power_to_raw,
)


def blocking_sync(
    port,
    key: int | None,
    *,
    attempts: int,
    read_timeout: float,
    inter_attempt_delay: float,
) -> None:
    """Synchronize the ET312 serial stream."""
    payload = bytes(apply_cipher([0x00], key))
    original_timeout = port.timeout
    port.timeout = read_timeout
    try:
        for _ in range(attempts):
            port.write(payload)
            port.flush()
            try:
                response = port.read(1)
            except serial.SerialException:
                response = b""
            if response == b"\x07":
                return
            if inter_attempt_delay:
                time.sleep(inter_attempt_delay)
    finally:
        try:
            port.timeout = original_timeout
        except (serial.SerialException, OSError):
            pass
    raise RuntimeError("ET312 sync failed")


def blocking_setup_key(port, *, timeout: float) -> int:
    """Negotiate the ET312 key."""
    original_timeout = port.timeout
    port.timeout = timeout
    try:
        command = [0x2F, 0x00]
        payload = command + [calculate_checksum(command)]
        port.write(payload)
        port.flush()
        response = list(port.read(3))
        if len(response) != 3:
            raise RuntimeError("ET312 key exchange timed out")
        if calculate_checksum(response[:-1]) != response[-1]:
            raise RuntimeError("ET312 key exchange checksum mismatch")
        if response[0] != 0x21:
            raise RuntimeError(f"Unexpected ET312 key exchange response: {response!r}")
        return response[1]
    finally:
        try:
            port.timeout = original_timeout
        except (serial.SerialException, OSError):
            pass


class Bridge:
    """Blocking ET312 bridge process that mirrors state over MQTT."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.serial_port: serial.Serial | None = None
        self.device_key: int | None = None
        self.cipher_key: int | None = None
        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if args.username:
            self.mqtt.username_pw_set(args.username, args.password)
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_message = self._on_message

    def _log(self, message: str) -> None:
        """Write a bridge log line to stderr."""
        print(f"[et312-bridge] {message}", file=sys.stderr, flush=True)

    def _open_serial(self) -> None:
        """Open the serial device."""
        self.serial_port = serial.Serial(
            self.args.device,
            self.args.baudrate,
            timeout=self.args.timeout,
            write_timeout=self.args.timeout,
            parity=serial.PARITY_NONE,
            bytesize=serial.EIGHTBITS,
            stopbits=serial.STOPBITS_ONE,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )

    def _reset_serial_buffers(self) -> None:
        """Best-effort serial buffer reset.

        RFCOMM-backed tty devices can throw low-level I/O errors here after a
        failed attempt. Treat that as a reconnect condition, not a fatal crash.
        """
        assert self.serial_port is not None
        try:
            self.serial_port.reset_input_buffer()
            self.serial_port.reset_output_buffer()
        except (serial.SerialException, OSError) as err:
            raise RuntimeError(f"serial buffer reset failed: {err}") from err

    def _close_serial(self) -> None:
        """Close the serial device if it is open."""
        if self.serial_port is None:
            return
        try:
            self.serial_port.close()
        finally:
            self.serial_port = None

    def connect(self) -> None:
        """Connect serial and MQTT layers."""
        last_error: Exception | None = None
        for attempt in range(1, self.args.connect_retries + 1):
            self._close_serial()
            self.device_key = None
            self.cipher_key = None
            try:
                self._log(
                    f"Connect attempt {attempt}/{self.args.connect_retries} "
                    f"on {self.args.device} at {self.args.baudrate} baud"
                )
                self._open_serial()
                assert self.serial_port is not None
                if self.args.startup_delay:
                    self._log(f"Waiting {self.args.startup_delay:.2f}s for serial link to settle")
                    time.sleep(self.args.startup_delay)
                self._reset_serial_buffers()
                self._log(
                    f"Trying sync with {self.args.sync_attempts} attempts, "
                    f"read timeout {self.args.sync_read_timeout:.2f}s"
                )
                blocking_sync(
                    self.serial_port,
                    None,
                    attempts=self.args.sync_attempts,
                    read_timeout=self.args.sync_read_timeout,
                    inter_attempt_delay=self.args.sync_inter_attempt_delay,
                )
                if self.args.post_sync_delay:
                    time.sleep(self.args.post_sync_delay)
                self.device_key = blocking_setup_key(
                    self.serial_port,
                    timeout=self.args.key_exchange_timeout,
                )
                self.cipher_key = self.device_key ^ 0x55
                self._log(f"Connected; negotiated ET312 key 0x{self.device_key:02X}")
                break
            except (serial.SerialException, RuntimeError) as err:
                last_error = err
                self._log(f"Attempt {attempt} failed: {err}")
                if attempt >= self.args.connect_retries:
                    raise RuntimeError(
                        f"ET312 connection failed after {attempt} attempt(s): {err}"
                    ) from err
                self._log(
                    f"Retrying after {self.args.reconnect_delay:.2f}s"
                )
                time.sleep(self.args.reconnect_delay)
        assert self.serial_port is not None
        self.mqtt.will_set(self.args.availability_topic, "offline", retain=True)
        self.mqtt.connect(self.args.mqtt_host, self.args.mqtt_port, 60)
        self.mqtt.loop_start()
        self.mqtt.publish(self.args.availability_topic, "online", retain=True)
        self.publish_state()

    def close(self) -> None:
        """Close the bridge cleanly."""
        try:
            if self.cipher_key is not None:
                self._write_register(0x4213, [0x00])
        finally:
            self.mqtt.publish(self.args.availability_topic, "offline", retain=True)
            self.mqtt.loop_stop()
            self.mqtt.disconnect()
            self._close_serial()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        """Subscribe to command topic on connect."""
        client.subscribe(self.args.command_topic)

    def _on_message(self, client, userdata, msg) -> None:
        """Handle Home Assistant commands."""
        payload = json.loads(msg.payload.decode("utf-8"))
        command = payload["command"]
        if command == "set_mode":
            self._set_mode(str(payload["mode"]))
        elif command == "set_power":
            self._set_power(str(payload["channel"]), int(payload["value"]))
        elif command == "request_state":
            pass
        else:
            raise RuntimeError(f"Unsupported ET312 bridge command: {command}")
        self.publish_state()

    def _read_register(self, address: int) -> int:
        payload = build_read_command(address)
        assert self.serial_port is not None
        self.serial_port.write(bytes(apply_cipher(payload, self.cipher_key)))
        response = list(self.serial_port.read(3))
        if len(response) != 3:
            raise RuntimeError(f"Timed out reading ET312 register 0x{address:04X}")
        return decode_read_response(response)

    def _write_register(self, address: int, values: list[int]) -> None:
        payload = build_write_command(address, values)
        assert self.serial_port is not None
        self.serial_port.write(bytes(apply_cipher(payload, self.cipher_key)))
        ack = self.serial_port.read(1)
        if ack != b"\x06":
            raise RuntimeError(f"Unexpected ET312 write ack for 0x{address:04X}: {ack!r}")

    def _set_mode(self, mode_name: str) -> None:
        for code, name in MODES.items():
            if name == mode_name:
                self._write_register(0x407B, [code])
                self._write_register(0x4070, [0x04, 0x12])
                time.sleep(0.02)
                return
        raise RuntimeError(f"Unsupported ET312 mode: {mode_name}")

    def _set_power(self, channel: str, value: int) -> None:
        base = 0x4000 if channel == "a" else 0x4100
        raw = ui_power_to_raw(value)
        self._write_register(base + 0xAC, [0x00])
        self._write_register(base + 0xA8, [0x00, 0x00])
        self._write_register(base + 0xA5, [raw])

    def publish_state(self) -> None:
        """Publish the current ET312 state as retained JSON."""
        mode_code = self._read_register(0x407B)
        payload = {
            "connected": True,
            "mode_code": mode_code,
            "mode": MODES.get(mode_code, f"Unknown (0x{mode_code:02X})"),
            "power_level_a": raw_byte_to_ui_99(self._read_register(0x4064)),
            "power_level_b": raw_byte_to_ui_99(self._read_register(0x4065)),
            "battery_percent": raw_byte_to_ui_99(self._read_register(0x4203)),
            "multi_adjust": raw_byte_to_ui_99(self._read_register(0x420D)),
            "available_modes": [MODES[code] for code in sorted(MODES)],
        }
        self.mqtt.publish(self.args.state_topic, json.dumps(payload), retain=True)


def parse_args() -> argparse.Namespace:
    """Parse bridge CLI arguments."""
    parser = argparse.ArgumentParser(description="ET312 MQTT bridge")
    parser.add_argument("device", help="Serial device path for the ET312")
    parser.add_argument("--baudrate", type=int, default=19200)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--state-topic", default="et312/state")
    parser.add_argument("--command-topic", default="et312/command")
    parser.add_argument("--availability-topic", default="et312/availability")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--startup-delay", type=float, default=1.5)
    parser.add_argument("--sync-attempts", type=int, default=40)
    parser.add_argument("--sync-read-timeout", type=float, default=0.35)
    parser.add_argument("--sync-inter-attempt-delay", type=float, default=0.1)
    parser.add_argument("--post-sync-delay", type=float, default=0.2)
    parser.add_argument("--key-exchange-timeout", type=float, default=1.5)
    parser.add_argument("--connect-retries", type=int, default=1)
    parser.add_argument("--reconnect-delay", type=float, default=2.0)
    return parser.parse_args()


def main() -> None:
    """Run the bridge until interrupted."""
    args = parse_args()
    bridge = Bridge(args)
    bridge.connect()
    try:
        while True:
            bridge.publish_state()
            time.sleep(args.poll_interval)
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
