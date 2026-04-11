"""Simple ET312-to-MQTT bridge."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import serial

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.et312.const import (
    CONTROL_FLAG_DISABLE_KNOBS,
    MODES,
    MULTI_ADJUST_UI_MAX,
    MULTI_ADJUST_UI_MIN,
    REG_CHANNEL_A_LEVEL,
    REG_CHANNEL_B_LEVEL,
    REG_CONTROL_FLAGS,
    REG_MULTI_ADJUST_RANGE_MAX,
    REG_MULTI_ADJUST_RANGE_MIN,
    REG_MULTI_ADJUST_VALUE,
)
from custom_components.et312.et312 import (
    apply_cipher,
    build_cipher_mask,
    build_read_command,
    build_write_command,
    calculate_checksum,
    decode_read_response,
    multi_adjust_bounds,
    raw_byte_to_ui_99,
    raw_level_byte_to_ui_99,
    raw_multi_adjust_to_ui_percent,
    ui_99_to_raw_byte,
    ui_multi_adjust_to_raw_byte,
)
from custom_components.et312.topics import (
    normalize_device_id,
    resolve_bridge_device_id,
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
    timeout_changed = False
    try:
        port.timeout = read_timeout
        timeout_changed = True
    except (serial.SerialException, OSError, ValueError):
        pass
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
        if timeout_changed:
            try:
                port.timeout = original_timeout
            except (serial.SerialException, OSError, ValueError):
                pass
    raise RuntimeError("ET312 sync failed")


def blocking_setup_key(port, *, timeout: float) -> int:
    """Negotiate the ET312 key."""
    original_timeout = port.timeout
    timeout_changed = False
    try:
        port.timeout = timeout
        timeout_changed = True
    except (serial.SerialException, OSError, ValueError):
        pass
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
        if timeout_changed:
            try:
                port.timeout = original_timeout
            except (serial.SerialException, OSError, ValueError):
                pass


class Bridge:
    """Blocking ET312 bridge process that mirrors state over MQTT."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.serial_port: serial.Serial | None = None
        self.host_key = 0x00
        self.box_key: int | None = None
        self.cipher_mask: int | None = None
        self.last_cipher_mask: int | None = None
        self.current_control_flags: int | None = None
        self.multi_adjust_mode_code: int | None = None
        self.multi_adjust_raw_bounds: tuple[int, int] | None = None
        self.last_published_payload: dict[str, object] | None = None
        self._publish_lock = threading.RLock()
        self._serial_lock = threading.RLock()
        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if args.username:
            self.mqtt.username_pw_set(args.username, args.password)
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_disconnect = self._on_disconnect
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
        failed attempt, or even during initial setup on flaky Bluetooth links.
        Treat that as advisory rather than fatal: we can still try sync.
        """
        assert self.serial_port is not None
        try:
            self.serial_port.reset_input_buffer()
            self.serial_port.reset_output_buffer()
        except Exception as err:
            self._log(f"Skipping serial buffer reset after error: {err}")

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
            self.box_key = None
            self.cipher_mask = None
            self.current_control_flags = None
            self.multi_adjust_mode_code = None
            self.multi_adjust_raw_bounds = None
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
                sync_masks = (
                    None,
                    self.last_cipher_mask,
                    build_cipher_mask(self.host_key, 0x00),
                )
                sync_ok = False
                sync_errors: list[str] = []
                for candidate_mask in sync_masks:
                    try:
                        blocking_sync(
                            self.serial_port,
                            candidate_mask,
                            attempts=self.args.sync_attempts,
                            read_timeout=self.args.sync_read_timeout,
                            inter_attempt_delay=self.args.sync_inter_attempt_delay,
                        )
                        self.cipher_mask = candidate_mask
                        sync_ok = True
                        self._log(f"Sync succeeded with mask {candidate_mask!r}")
                        break
                    except RuntimeError as err:
                        sync_errors.append(f"mask={candidate_mask!r}: {err}")
                if not sync_ok:
                    raise RuntimeError(
                        "ET312 sync failed across mask strategies "
                        f"({'; '.join(sync_errors)})"
                    )
                if self.args.post_sync_delay:
                    time.sleep(self.args.post_sync_delay)
                try:
                    self.box_key = blocking_setup_key(
                        self.serial_port,
                        timeout=self.args.key_exchange_timeout,
                    )
                except RuntimeError:
                    self._log(
                        "Key exchange timed out; resyncing before assuming ET312 box key 0x00"
                    )
                    self.cipher_mask = None
                    self._reset_serial_buffers()
                    blocking_sync(
                        self.serial_port,
                        None,
                        attempts=self.args.sync_attempts,
                        read_timeout=self.args.sync_read_timeout,
                        inter_attempt_delay=self.args.sync_inter_attempt_delay,
                    )
                    self.box_key = 0x00
                    self.cipher_mask = build_cipher_mask(
                        self.host_key,
                        self.box_key,
                    )
                    self.last_cipher_mask = self.cipher_mask
                    self._log("Assuming ET312 box key 0x00")
                self.cipher_mask = build_cipher_mask(self.host_key, self.box_key)
                self.last_cipher_mask = self.cipher_mask
                self._log(
                    "Connected; negotiated ET312 box key "
                    f"0x{self.box_key:02X} with outbound mask 0x{self.cipher_mask:02X}"
                )
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
        self._log(
            f"Connecting to MQTT broker {self.args.mqtt_host}:{self.args.mqtt_port}"
        )
        connect_rc = self.mqtt.connect(self.args.mqtt_host, self.args.mqtt_port, 60)
        self._log(
            f"MQTT connect returned {connect_rc}; "
            f"socket_open={bool(self.mqtt.socket())}"
        )
        self.mqtt.loop_start()
        self._log("MQTT loop started")
        availability_info = self.mqtt.publish(
            self.args.availability_topic,
            "online",
            retain=True,
        )
        self._log(f"Published availability with rc={availability_info.rc}")
        self.publish_state(force=True)

    def close(self) -> None:
        """Close the bridge cleanly."""
        try:
            if self.cipher_mask is not None:
                self._write_register(0x4213, [0x00])
        finally:
            self.mqtt.publish(self.args.availability_topic, "offline", retain=True)
            self.mqtt.loop_stop()
            self.mqtt.disconnect()
            self._close_serial()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        """Subscribe to command topic on connect."""
        self._log(f"MQTT connected with reason code {reason_code}")
        client.subscribe(self.args.command_topic)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        """Log MQTT disconnects for troubleshooting."""
        self._log(f"MQTT disconnected with reason code {reason_code}")

    def _on_message(self, client, userdata, msg) -> None:
        """Handle Home Assistant commands."""
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            command = payload["command"]
            payload_device_id = payload.get("device_id")
            if payload_device_id is not None:
                normalized_payload_device_id = normalize_device_id(str(payload_device_id))
                if normalized_payload_device_id != self.args.device_id:
                    raise RuntimeError(
                        "Ignoring command for "
                        f"{normalized_payload_device_id}; this bridge is {self.args.device_id}"
                    )
            self._log(f"Received command on {msg.topic}: {payload}")
            force_publish = False
            burst_publish = False
            with self._serial_lock:
                if command == "set_mode":
                    self._set_mode(str(payload["mode"]))
                    burst_publish = True
                elif command in ("set_power", "set_channel_power"):
                    self._set_power(str(payload["channel"]), int(payload["value"]))
                    burst_publish = True
                elif command == "set_multi_adjust":
                    self._set_multi_adjust(int(payload["value"]))
                    burst_publish = True
                elif command == "set_front_panel_controls_disabled":
                    self._set_front_panel_controls_disabled(bool(payload["value"]))
                    burst_publish = True
                elif command == "request_state":
                    force_publish = True
                else:
                    raise RuntimeError(f"Unsupported ET312 bridge command: {command}")
            if force_publish:
                self.publish_state(force=True)
            elif burst_publish:
                self.publish_state_burst()
        except Exception as err:
            self._log(f"Command handling failed: {err}")

    def _read_register(self, address: int) -> int:
        with self._serial_lock:
            payload = build_read_command(address)
            assert self.serial_port is not None
            self.serial_port.write(bytes(apply_cipher(payload, self.cipher_mask)))
            response = list(self.serial_port.read(3))
            if len(response) != 3:
                raise RuntimeError(f"Timed out reading ET312 register 0x{address:04X}")
            return decode_read_response(response)

    def _write_register(self, address: int, values: list[int]) -> None:
        with self._serial_lock:
            payload = build_write_command(address, values)
            assert self.serial_port is not None
            self.serial_port.write(bytes(apply_cipher(payload, self.cipher_mask)))
            ack = self.serial_port.read(1)
            if ack != b"\x06":
                raise RuntimeError(f"Unexpected ET312 write ack for 0x{address:04X}: {ack!r}")

    def _get_control_flags(self) -> int:
        """Return current control flags."""
        if self.current_control_flags is not None:
            return self.current_control_flags

        flags = self._read_register(REG_CONTROL_FLAGS)
        self.current_control_flags = flags
        return flags

    def _get_multi_adjust_bounds(self, mode_code: int | None = None) -> tuple[int, int]:
        """Return cached multi-adjust bounds, refreshing them when mode changes."""
        if (
            self.multi_adjust_raw_bounds is not None
            and (mode_code is None or self.multi_adjust_mode_code == mode_code)
        ):
            return self.multi_adjust_raw_bounds

        raw_min = self._read_register(REG_MULTI_ADJUST_RANGE_MIN)
        raw_max = self._read_register(REG_MULTI_ADJUST_RANGE_MAX)
        self.multi_adjust_mode_code = mode_code
        self.multi_adjust_raw_bounds = multi_adjust_bounds(raw_min, raw_max)
        return self.multi_adjust_raw_bounds

    def _set_control_flags(self, desired_flags: int) -> None:
        """Write ET312 control flags when the value changes."""
        current_flags = self._get_control_flags()
        if desired_flags == current_flags:
            return

        self._write_register(REG_CONTROL_FLAGS, [desired_flags])
        self.current_control_flags = desired_flags

    def _set_front_panel_controls_disabled(self, disabled: bool) -> None:
        """Enable or disable the ET312 front-panel knobs."""
        current_flags = self._get_control_flags()
        if disabled:
            desired_flags = current_flags | CONTROL_FLAG_DISABLE_KNOBS
        else:
            desired_flags = current_flags & ~CONTROL_FLAG_DISABLE_KNOBS
        self._set_control_flags(desired_flags)

    def _set_mode(self, mode_name: str) -> None:
        for code, name in MODES.items():
            if name == mode_name:
                self._write_register(0x407B, [code])
                self._write_register(0x4070, [0x04, 0x12])
                self.multi_adjust_mode_code = None
                self.multi_adjust_raw_bounds = None
                time.sleep(0.02)
                return
        raise RuntimeError(f"Unsupported ET312 mode: {mode_name}")

    def _set_power(self, channel: str, value: int) -> None:
        if not 0 <= value <= 99:
            raise RuntimeError(f"Unsupported ET312 power level: {value}")
        if channel == "a":
            level_register = REG_CHANNEL_A_LEVEL
        elif channel == "b":
            level_register = REG_CHANNEL_B_LEVEL
        else:
            raise RuntimeError(f"Unsupported ET312 channel: {channel}")

        current_flags = self._get_control_flags()
        self._set_control_flags(current_flags | CONTROL_FLAG_DISABLE_KNOBS)
        self._write_register(level_register, [ui_99_to_raw_byte(value)])

    def _set_multi_adjust(self, value: int) -> None:
        if value < MULTI_ADJUST_UI_MIN or value > MULTI_ADJUST_UI_MAX:
            raise RuntimeError(f"Unsupported ET312 multi-adjust value: {value}")
        raw_min, raw_max = self._get_multi_adjust_bounds()
        current_flags = self._get_control_flags()
        self._set_control_flags(current_flags | CONTROL_FLAG_DISABLE_KNOBS)
        self._write_register(
            REG_MULTI_ADJUST_VALUE,
            [ui_multi_adjust_to_raw_byte(value, raw_min, raw_max)],
        )

    def read_state_payload(self) -> dict[str, object]:
        """Read the current ET312 state as a normalized MQTT payload."""
        with self._serial_lock:
            mode_code = self._read_register(0x407B)
            control_flags = self._read_register(REG_CONTROL_FLAGS)
            multi_adjust_raw_min, multi_adjust_raw_max = self._get_multi_adjust_bounds(
                mode_code
            )
            self.current_control_flags = control_flags
            return {
                "connected": True,
                "device_id": self.args.device_id,
                "mode_code": mode_code,
                "mode": MODES.get(mode_code, f"Unknown (0x{mode_code:02X})"),
                "power_level_a": raw_level_byte_to_ui_99(self._read_register(REG_CHANNEL_A_LEVEL)),
                "power_level_b": raw_level_byte_to_ui_99(self._read_register(REG_CHANNEL_B_LEVEL)),
                "battery_percent": raw_byte_to_ui_99(self._read_register(0x4203)),
                "multi_adjust": raw_multi_adjust_to_ui_percent(
                    self._read_register(REG_MULTI_ADJUST_VALUE),
                    multi_adjust_raw_min,
                    multi_adjust_raw_max,
                ),
                "front_panel_controls_disabled": bool(control_flags & CONTROL_FLAG_DISABLE_KNOBS),
            }

    def publish_state(self, *, force: bool = False) -> bool:
        """Publish current ET312 state when changed or explicitly requested."""
        with self._publish_lock:
            payload = self.read_state_payload()
            if not force and payload == self.last_published_payload:
                return False

            publish_info = self.mqtt.publish(
                self.args.state_topic,
                json.dumps(payload),
                retain=True,
            )
            self.last_published_payload = dict(payload)

        self._log(
            "Published state with rc="
            f"{publish_info.rc}: mode={payload['mode']} "
            f"A={payload['power_level_a']} "
            f"B={payload['power_level_b']} "
            f"MA={payload['multi_adjust']} "
            f"battery={payload['battery_percent']}"
        )
        return True

    def publish_state_burst(self) -> bool:
        """Publish a short state burst after a detected payload change."""
        if not self.publish_state():
            return False
        for _ in range(1, self.args.change_burst_count):
            time.sleep(self.args.change_burst_interval)
            self.publish_state(force=True)
        return True


def positive_float(value: str) -> float:
    """Parse a positive floating-point CLI value."""
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def positive_int(value: str) -> int:
    """Parse a positive integer CLI value."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


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
    parser.add_argument("--device-id", default="")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--change-burst-count", type=positive_int, default=3)
    parser.add_argument("--change-burst-interval", type=positive_float, default=1.0)
    parser.add_argument("--startup-delay", type=float, default=1.5)
    parser.add_argument("--sync-attempts", type=int, default=40)
    parser.add_argument("--sync-read-timeout", type=float, default=0.35)
    parser.add_argument("--sync-inter-attempt-delay", type=float, default=0.1)
    parser.add_argument("--post-sync-delay", type=float, default=0.2)
    parser.add_argument("--key-exchange-timeout", type=float, default=1.5)
    parser.add_argument("--connect-retries", type=int, default=1)
    parser.add_argument("--reconnect-delay", type=float, default=2.0)
    args = parser.parse_args()
    args.device_id = resolve_bridge_device_id(args.device_id, args.state_topic)
    return args


def main() -> None:
    """Run the bridge until interrupted."""
    args = parse_args()
    bridge = Bridge(args)
    bridge.connect()
    try:
        while True:
            bridge.publish_state_burst()
            time.sleep(args.poll_interval)
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
