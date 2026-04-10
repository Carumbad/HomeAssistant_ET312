"""Manage Raspberry Pi ET312 bridge device configs, units, and discovery."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import serial
except ImportError:  # pragma: no cover - available on the Pi/bridge host
    serial = None

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.et312.const import REG_BATTERY_PERCENT, REG_CURRENT_MODE
from custom_components.et312.et312 import (
    ET312ConnectionError,
    apply_cipher,
    build_cipher_mask,
    build_read_command,
    calculate_checksum,
    decode_read_response,
)

DEFAULT_INSTALL_DIR = Path("/opt/et312-mqtt-bridge")
DEFAULT_SYSTEMD_DIR = Path("/etc/systemd/system")
DEFAULT_BRIDGE_CONFIG_NAME = "et312-bridge.env"
DEFAULT_DISCOVERY_CONFIG_NAME = "et312-discovery.env"
DEFAULT_DEVICES_DIR_NAME = "devices"
DEFAULT_TOPIC_PREFIX = "et312"
DEFAULT_DISCOVERY_PATTERNS = ("Micro", "312")
DEFAULT_DISCOVERY_SCAN_SECONDS = 15
DEFAULT_BAUDRATE = 19200
DEFAULT_TIMEOUT = 1.0
DEFAULT_STARTUP_DELAY = 2.0
DEFAULT_SYNC_ATTEMPTS = 40
DEFAULT_SYNC_READ_TIMEOUT = 0.35
DEFAULT_SYNC_GAP = 0.1
DEFAULT_POST_SYNC_DELAY = 0.2
DEFAULT_KEY_TIMEOUT = 1.5
DEFAULT_CONNECT_RETRIES = 4
DEFAULT_RECONNECT_DELAY = 3.0


def blocking_sync(
    port: Any,
    mask: int | None,
    *,
    attempts: int,
    read_timeout: float,
    inter_attempt_delay: float,
) -> None:
    """Synchronize the ET312 serial stream."""
    payload = bytes(apply_cipher([0x00], mask))
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
    raise ET312ConnectionError("ET312 sync failed")


def blocking_setup_key(port: serial.Serial, *, timeout: float) -> int:
    """Negotiate the ET312 box key."""
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
        port.write(bytes(payload))
        port.flush()
        response = list(port.read(3))
        if len(response) != 3:
            raise ET312ConnectionError("ET312 key exchange timed out")
        if calculate_checksum(response[:-1]) != response[-1]:
            raise ET312ConnectionError("ET312 key exchange checksum mismatch")
        if response[0] != 0x21:
            raise ET312ConnectionError(
                f"Unexpected ET312 key exchange response: {response!r}"
            )
        return response[1]
    finally:
        if timeout_changed:
            try:
                port.timeout = original_timeout
            except (serial.SerialException, OSError, ValueError):
                pass


def quote_env(value: str) -> str:
    """Quote an env file value safely for shell sourcing."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a small KEY="VALUE" env file."""
    if not path.exists():
        return {}

    data: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        value = bytes(value, "utf-8").decode("unicode_escape")
        data[key] = value
    return data


def write_env_file(path: Path, values: dict[str, str]) -> None:
    """Write a deterministic env file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{key}={quote_env(values[key])}\n" for key in sorted(values))
    path.write_text(body, encoding="utf-8")


def install_paths(install_dir: Path) -> dict[str, Path]:
    """Return important install/config paths."""
    config_dir = install_dir / "config"
    return {
        "install_dir": install_dir,
        "config_dir": config_dir,
        "devices_dir": config_dir / DEFAULT_DEVICES_DIR_NAME,
        "bridge_config": config_dir / DEFAULT_BRIDGE_CONFIG_NAME,
        "discovery_config": config_dir / DEFAULT_DISCOVERY_CONFIG_NAME,
    }


def ensure_layout(install_dir: Path) -> None:
    """Ensure the multi-device config layout exists with default global config."""
    paths = install_paths(install_dir)
    paths["config_dir"].mkdir(parents=True, exist_ok=True)
    paths["devices_dir"].mkdir(parents=True, exist_ok=True)

    bridge_defaults = {
        "MQTT_HOST": "127.0.0.1",
        "MQTT_PORT": "1883",
        "MQTT_USERNAME": "",
        "MQTT_PASSWORD": "",
        "MQTT_TOPIC_PREFIX": DEFAULT_TOPIC_PREFIX,
        "POLL_INTERVAL": "2.0",
        "TIMEOUT": str(DEFAULT_TIMEOUT),
        "BAUDRATE": str(DEFAULT_BAUDRATE),
        "STARTUP_DELAY": str(DEFAULT_STARTUP_DELAY),
        "SYNC_ATTEMPTS": str(DEFAULT_SYNC_ATTEMPTS),
        "SYNC_READ_TIMEOUT": str(DEFAULT_SYNC_READ_TIMEOUT),
        "SYNC_INTER_ATTEMPT_DELAY": str(DEFAULT_SYNC_GAP),
        "POST_SYNC_DELAY": str(DEFAULT_POST_SYNC_DELAY),
        "KEY_EXCHANGE_TIMEOUT": str(DEFAULT_KEY_TIMEOUT),
        "CONNECT_RETRIES": str(DEFAULT_CONNECT_RETRIES),
        "RECONNECT_DELAY": str(DEFAULT_RECONNECT_DELAY),
    }
    if not paths["bridge_config"].exists():
        write_env_file(paths["bridge_config"], bridge_defaults)
    else:
        existing = parse_env_file(paths["bridge_config"])
        merged = {**bridge_defaults, **existing}
        write_env_file(paths["bridge_config"], merged)

    discovery_defaults = {
        "DISCOVERY_NAME_PATTERNS": ",".join(DEFAULT_DISCOVERY_PATTERNS),
        "DISCOVERY_SCAN_SECONDS": str(DEFAULT_DISCOVERY_SCAN_SECONDS),
    }
    if not paths["discovery_config"].exists():
        write_env_file(paths["discovery_config"], discovery_defaults)
    else:
        existing = parse_env_file(paths["discovery_config"])
        merged = {**discovery_defaults, **existing}
        write_env_file(paths["discovery_config"], merged)


def normalize_mac(mac: str) -> str:
    """Canonicalize a Bluetooth MAC address."""
    hex_only = re.sub(r"[^0-9A-Fa-f]", "", mac)
    if len(hex_only) != 12:
        raise ValueError(f"Invalid Bluetooth MAC address: {mac}")
    pairs = [hex_only[idx : idx + 2].upper() for idx in range(0, 12, 2)]
    return ":".join(pairs)


def device_id_from_mac(mac: str) -> str:
    """Build a stable ET312 id from the last six MAC hex chars."""
    suffix = normalize_mac(mac).replace(":", "")[-6:]
    return f"ET312_{suffix}"


def slugify_identifier(value: str) -> str:
    """Turn an arbitrary value into a service/config-safe identifier chunk."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return cleaned.upper() or "DEVICE"


def device_id_from_serial(device: str) -> str:
    """Build a deterministic id for non-Bluetooth devices."""
    return f"ET312_{slugify_identifier(Path(device).name)}"


def parse_patterns(raw_value: str) -> tuple[str, ...]:
    """Parse comma-separated discovery fragments."""
    return tuple(part.strip() for part in raw_value.split(",") if part.strip())


def device_config_path(install_dir: Path, device_id: str) -> Path:
    """Return the env-file path for one configured device."""
    return install_paths(install_dir)["devices_dir"] / f"{device_id}.env"


def list_device_config_paths(install_dir: Path) -> list[Path]:
    """Return all configured device env files."""
    devices_dir = install_paths(install_dir)["devices_dir"]
    if not devices_dir.exists():
        return []
    return sorted(path for path in devices_dir.glob("*.env") if path.is_file())


def load_device_configs(install_dir: Path) -> list[dict[str, str]]:
    """Load all configured devices."""
    devices: list[dict[str, str]] = []
    for path in list_device_config_paths(install_dir):
        data = parse_env_file(path)
        if not data:
            continue
        data["CONFIG_PATH"] = str(path)
        devices.append(data)
    return devices


def next_rfcomm_device(install_dir: Path) -> str:
    """Choose the next unused /dev/rfcommN path."""
    used: set[int] = set()
    for device in load_device_configs(install_dir):
        rfcomm_path = device.get("RFCOMM_DEVICE", "")
        match = re.fullmatch(r"/dev/rfcomm(\d+)", rfcomm_path)
        if match:
            used.add(int(match.group(1)))
    for path in Path("/dev").glob("rfcomm[0-9]*"):
        match = re.fullmatch(r"rfcomm(\d+)", path.name)
        if match:
            used.add(int(match.group(1)))
    try:
        rfcomm_status = run_command(["rfcomm"], check=False)
    except FileNotFoundError:
        rfcomm_status = None
    if rfcomm_status is not None:
        for match in re.finditer(r"rfcomm(\d+):", rfcomm_status.stdout):
            used.add(int(match.group(1)))
    next_idx = 0
    while next_idx in used:
        next_idx += 1
    return f"/dev/rfcomm{next_idx}"


def bridge_topic_defaults(device_id: str, topic_prefix: str) -> dict[str, str]:
    """Build default MQTT topics for a device."""
    base = f"{topic_prefix.rstrip('/')}/{device_id}"
    return {
        "MQTT_STATE_TOPIC": f"{base}/state",
        "MQTT_COMMAND_TOPIC": f"{base}/command",
        "MQTT_AVAILABILITY_TOPIC": f"{base}/availability",
    }


def merge_bridge_defaults(
    install_dir: Path,
    device_values: dict[str, str],
) -> dict[str, str]:
    """Fill in per-device bridge settings from the shared bridge defaults."""
    bridge_defaults = parse_env_file(install_paths(install_dir)["bridge_config"])
    topic_prefix = device_values.get(
        "MQTT_TOPIC_PREFIX",
        bridge_defaults.get("MQTT_TOPIC_PREFIX", DEFAULT_TOPIC_PREFIX),
    )
    merged = {
        "DEVICE_ENABLED": "1",
        **bridge_defaults,
        **device_values,
        **bridge_topic_defaults(device_values["DEVICE_ID"], topic_prefix),
        **device_values,
    }
    if not merged.get("DEVICE") and merged.get("RFCOMM_DEVICE"):
        merged["DEVICE"] = merged["RFCOMM_DEVICE"]
    return merged


def register_serial_device(
    install_dir: Path,
    *,
    device: str,
    device_id: str | None,
) -> str:
    """Register one directly attached serial ET312."""
    ensure_layout(install_dir)
    resolved_id = device_id or device_id_from_serial(device)
    existing = parse_env_file(device_config_path(install_dir, resolved_id))
    values = {
        **existing,
        "DEVICE_ID": resolved_id,
        "DEVICE_NAME": resolved_id,
        "DEVICE_TRANSPORT": "serial",
        "DEVICE": device,
    }
    merged = merge_bridge_defaults(install_dir, values)
    write_env_file(device_config_path(install_dir, resolved_id), merged)
    return resolved_id


def register_bluetooth_device(
    install_dir: Path,
    *,
    mac: str,
    rfcomm_device: str,
    rfcomm_channel: str,
    bluetooth_name: str | None,
    device_id: str | None,
) -> str:
    """Register one Bluetooth ET312."""
    ensure_layout(install_dir)
    normalized_mac = normalize_mac(mac)
    resolved_id = device_id or device_id_from_mac(normalized_mac)
    existing = parse_env_file(device_config_path(install_dir, resolved_id))
    values = {
        **existing,
        "DEVICE_ID": resolved_id,
        "DEVICE_NAME": resolved_id,
        "DEVICE_TRANSPORT": "bluetooth",
        "DEVICE": rfcomm_device,
        "ET312_BLUETOOTH_MAC": normalized_mac,
        "RFCOMM_DEVICE": rfcomm_device,
        "RFCOMM_CHANNEL": str(rfcomm_channel),
    }
    if bluetooth_name:
        values["ET312_BLUETOOTH_NAME"] = bluetooth_name
    merged = merge_bridge_defaults(install_dir, values)
    write_env_file(device_config_path(install_dir, resolved_id), merged)
    return resolved_id


def load_enabled_devices(install_dir: Path) -> list[dict[str, str]]:
    """Return enabled devices only."""
    return [
        device
        for device in load_device_configs(install_dir)
        if device.get("DEVICE_ENABLED", "1") != "0"
    ]


def rfcomm_unit_name(device_id: str) -> str:
    """Return the RFCOMM unit name for one device."""
    return f"et312-rfcomm-{device_id}.service"


def bridge_unit_name(device_id: str) -> str:
    """Return the bridge unit name for one device."""
    return f"et312-mqtt-bridge-{device_id}.service"


def write_unit(path: Path, content: str) -> None:
    """Write one systemd unit file."""
    path.write_text(content, encoding="utf-8")


def generate_units(install_dir: Path, systemd_dir: Path) -> list[str]:
    """Generate per-device RFCOMM and bridge systemd units."""
    systemd_dir.mkdir(parents=True, exist_ok=True)
    active_names: set[str] = set()
    generated_units: list[str] = []

    for device in load_enabled_devices(install_dir):
        device_id = device["DEVICE_ID"]
        config_path = Path(device["CONFIG_PATH"])
        bridge_requires_rfcomm = bool(device.get("ET312_BLUETOOTH_MAC"))
        bridge_after = ["network-online.target"]
        bridge_wants = ["network-online.target"]
        if bridge_requires_rfcomm:
            bridge_after.append(rfcomm_unit_name(device_id))
            bridge_wants.append(rfcomm_unit_name(device_id))

            rfcomm_name = rfcomm_unit_name(device_id)
            rfcomm_content = f"""[Unit]
Description=ET312 Bluetooth RFCOMM Binding ({device_id})
After=bluetooth.service network.target
Requires=bluetooth.service

[Service]
Type=simple
Restart=always
RestartSec=2
ExecStart={install_dir}/scripts/run_et312_rfcomm.sh {config_path}
ExecStop={install_dir}/scripts/release_et312_rfcomm.sh {config_path}

[Install]
WantedBy=multi-user.target
"""
            write_unit(systemd_dir / rfcomm_name, rfcomm_content)
            generated_units.append(rfcomm_name)
            active_names.add(rfcomm_name)

        bridge_name = bridge_unit_name(device_id)
        bridge_content = f"""[Unit]
Description=ET312 MQTT Bridge ({device_id})
After={' '.join(bridge_after)}
Wants={' '.join(bridge_wants)}

[Service]
Type=simple
User=et312
Group=et312
SupplementaryGroups=dialout
Restart=always
RestartSec=5
ExecStart={install_dir}/scripts/run_et312_mqtt_bridge.sh {config_path}

[Install]
WantedBy=multi-user.target
"""
        write_unit(systemd_dir / bridge_name, bridge_content)
        generated_units.append(bridge_name)
        active_names.add(bridge_name)

    for pattern in ("et312-rfcomm-*.service", "et312-mqtt-bridge-*.service"):
        for stale_path in systemd_dir.glob(pattern):
            if stale_path.name not in active_names:
                stale_path.unlink(missing_ok=True)

    return generated_units


def migrate_legacy_config(install_dir: Path) -> list[str]:
    """Migrate the previous single-device config into the device registry."""
    ensure_layout(install_dir)
    created: list[str] = []
    if list_device_config_paths(install_dir):
        return created

    paths = install_paths(install_dir)
    legacy_bridge = paths["config_dir"] / "et312-mqtt-bridge.env"
    legacy_rfcomm = paths["config_dir"] / "et312-rfcomm.env"
    if not legacy_bridge.exists() and not legacy_rfcomm.exists():
        return created

    bridge_values = parse_env_file(legacy_bridge)
    rfcomm_values = parse_env_file(legacy_rfcomm)

    if bridge_values:
        bridge_global = parse_env_file(paths["bridge_config"])
        for key in (
            "MQTT_HOST",
            "MQTT_PORT",
            "MQTT_USERNAME",
            "MQTT_PASSWORD",
            "POLL_INTERVAL",
            "TIMEOUT",
            "BAUDRATE",
            "STARTUP_DELAY",
            "SYNC_ATTEMPTS",
            "SYNC_READ_TIMEOUT",
            "SYNC_INTER_ATTEMPT_DELAY",
            "POST_SYNC_DELAY",
            "KEY_EXCHANGE_TIMEOUT",
            "CONNECT_RETRIES",
            "RECONNECT_DELAY",
        ):
            if bridge_values.get(key):
                bridge_global[key] = bridge_values[key]
        if bridge_values.get("STATE_TOPIC", "").startswith("et312/"):
            bridge_global["MQTT_TOPIC_PREFIX"] = bridge_values["STATE_TOPIC"].split("/")[0]
        write_env_file(paths["bridge_config"], bridge_global)

    if rfcomm_values.get("ET312_BLUETOOTH_MAC"):
        device_id = register_bluetooth_device(
            install_dir,
            mac=rfcomm_values["ET312_BLUETOOTH_MAC"],
            rfcomm_device=rfcomm_values.get("RFCOMM_DEVICE", "/dev/rfcomm0"),
            rfcomm_channel=rfcomm_values.get("RFCOMM_CHANNEL", "2"),
            bluetooth_name=rfcomm_values.get("ET312_BLUETOOTH_NAME"),
            device_id=None,
        )
        device_values = parse_env_file(device_config_path(install_dir, device_id))
        if bridge_values.get("STATE_TOPIC"):
            device_values["MQTT_STATE_TOPIC"] = bridge_values["STATE_TOPIC"]
        if bridge_values.get("COMMAND_TOPIC"):
            device_values["MQTT_COMMAND_TOPIC"] = bridge_values["COMMAND_TOPIC"]
        if bridge_values.get("AVAILABILITY_TOPIC"):
            device_values["MQTT_AVAILABILITY_TOPIC"] = bridge_values["AVAILABILITY_TOPIC"]
        write_env_file(device_config_path(install_dir, device_id), device_values)
        created.append(device_id)
        return created

    if bridge_values.get("DEVICE"):
        device_id = register_serial_device(
            install_dir,
            device=bridge_values["DEVICE"],
            device_id=None,
        )
        device_values = parse_env_file(device_config_path(install_dir, device_id))
        if bridge_values.get("STATE_TOPIC"):
            device_values["MQTT_STATE_TOPIC"] = bridge_values["STATE_TOPIC"]
        if bridge_values.get("COMMAND_TOPIC"):
            device_values["MQTT_COMMAND_TOPIC"] = bridge_values["COMMAND_TOPIC"]
        if bridge_values.get("AVAILABILITY_TOPIC"):
            device_values["MQTT_AVAILABILITY_TOPIC"] = bridge_values["AVAILABILITY_TOPIC"]
        write_env_file(device_config_path(install_dir, device_id), device_values)
        created.append(device_id)

    return created


def run_command(command: list[str], *, check: bool = True, text: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and optionally raise on failure."""
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=text,
    )


def bluetoothctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run bluetoothctl with direct arguments."""
    return run_command(["bluetoothctl", *args], check=check)


def scan_bluetooth_devices(scan_seconds: int) -> list[tuple[str, str]]:
    """Scan and return candidate Bluetooth devices."""
    bluetoothctl("power", "on", check=False)
    bluetoothctl("agent", "on", check=False)
    bluetoothctl("default-agent", check=False)
    bluetoothctl("scan", "on", check=False)
    time.sleep(scan_seconds)
    bluetoothctl("scan", "off", check=False)
    output = bluetoothctl("devices").stdout
    devices: list[tuple[str, str]] = []
    for line in output.splitlines():
        match = re.match(r"Device\s+([0-9A-F:]{17})\s+(.+)$", line.strip(), re.IGNORECASE)
        if match:
            devices.append((normalize_mac(match.group(1)), match.group(2).strip()))
    return devices


def pair_and_trust_device(mac: str) -> None:
    """Pair and trust a Bluetooth device."""
    bluetoothctl("pair", mac, check=False)
    bluetoothctl("trust", mac, check=False)


def detect_rfcomm_channel(mac: str) -> str:
    """Detect an RFCOMM serial-port channel for a Bluetooth device."""
    result = run_command(["sdptool", "search", "--bdaddr", mac, "SP"], check=False)
    match = re.search(r"Channel:\s+(\d+)", result.stdout)
    if match:
        return match.group(1)
    return "2"


def wait_for_path(path: Path, timeout: float) -> bool:
    """Wait until a filesystem path appears."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.25)
    return path.exists()


def probe_et312_serial(device: str) -> dict[str, int]:
    """Confirm a candidate serial device really speaks ET312."""
    if serial is None:
        raise RuntimeError("pyserial is required for ET312 probing")
    port = serial.Serial(
        device,
        DEFAULT_BAUDRATE,
        timeout=DEFAULT_TIMEOUT,
        write_timeout=DEFAULT_TIMEOUT,
        parity=serial.PARITY_NONE,
        bytesize=serial.EIGHTBITS,
        stopbits=serial.STOPBITS_ONE,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )
    try:
        time.sleep(DEFAULT_STARTUP_DELAY)
        try:
            port.reset_input_buffer()
            port.reset_output_buffer()
        except Exception:
            pass

        for mask in (None, build_cipher_mask(0x00, 0x00)):
            try:
                blocking_sync(
                    port,
                    mask,
                    attempts=DEFAULT_SYNC_ATTEMPTS,
                    read_timeout=DEFAULT_SYNC_READ_TIMEOUT,
                    inter_attempt_delay=DEFAULT_SYNC_GAP,
                )
                break
            except RuntimeError:
                continue
        else:
            raise ET312ConnectionError("ET312 sync probe failed")

        time.sleep(DEFAULT_POST_SYNC_DELAY)
        try:
            box_key = blocking_setup_key(port, timeout=DEFAULT_KEY_TIMEOUT)
            cipher_mask = build_cipher_mask(0x00, box_key)
        except ET312ConnectionError:
            blocking_sync(
                port,
                None,
                attempts=DEFAULT_SYNC_ATTEMPTS,
                read_timeout=DEFAULT_SYNC_READ_TIMEOUT,
                inter_attempt_delay=DEFAULT_SYNC_GAP,
            )
            cipher_mask = build_cipher_mask(0x00, 0x00)

        def read_register(address: int) -> int:
            payload = build_read_command(address)
            port.write(bytes(apply_cipher(payload, cipher_mask)))
            response = list(port.read(3))
            if len(response) != 3:
                raise ET312ConnectionError(
                    f"Timed out probing ET312 register 0x{address:04X}"
                )
            return decode_read_response(response)

        return {
            "mode_code": read_register(REG_CURRENT_MODE),
            "battery_percent": read_register(REG_BATTERY_PERCENT),
        }
    finally:
        port.close()


def interrogate_bluetooth_candidate(
    *,
    mac: str,
    rfcomm_device: str,
    rfcomm_channel: str,
) -> dict[str, int]:
    """Bring up a temporary RFCOMM link and confirm the device is an ET312."""
    match = re.fullmatch(r"/dev/rfcomm(\d+)", rfcomm_device)
    if not match:
        raise ValueError(f"RFCOMM device must look like /dev/rfcommN, got: {rfcomm_device}")
    rfcomm_id = match.group(1)
    run_command(["rfcomm", "release", rfcomm_id], check=False)
    process = subprocess.Popen(
        ["rfcomm", "connect", rfcomm_id, mac, rfcomm_channel],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_path(Path(rfcomm_device), timeout=15.0):
            raise ET312ConnectionError(
                f"Timed out waiting for temporary RFCOMM device {rfcomm_device}"
            )
        return probe_et312_serial(rfcomm_device)
    finally:
        process.terminate()
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3.0)
        run_command(["rfcomm", "release", rfcomm_id], check=False)


def discover_bluetooth_devices(
    install_dir: Path,
    *,
    scan_seconds: int,
    name_patterns: tuple[str, ...],
) -> list[str]:
    """Discover, interrogate, and register matching ET312 Bluetooth devices."""
    ensure_layout(install_dir)
    registered_ids: list[str] = []
    patterns = tuple(pattern.lower() for pattern in name_patterns if pattern)
    for mac, name in scan_bluetooth_devices(scan_seconds):
        lower_name = name.lower()
        if patterns and not all(pattern in lower_name for pattern in patterns):
            continue
        pair_and_trust_device(mac)
        rfcomm_channel = detect_rfcomm_channel(mac)
        resolved_id = device_id_from_mac(mac)
        existing = parse_env_file(device_config_path(install_dir, resolved_id))
        rfcomm_device = existing.get("RFCOMM_DEVICE") or next_rfcomm_device(install_dir)
        interrogate_bluetooth_candidate(
            mac=mac,
            rfcomm_device=rfcomm_device,
            rfcomm_channel=rfcomm_channel,
        )
        register_bluetooth_device(
            install_dir,
            mac=mac,
            rfcomm_device=rfcomm_device,
            rfcomm_channel=rfcomm_channel,
            bluetooth_name=name,
            device_id=resolved_id,
        )
        registered_ids.append(resolved_id)
    return registered_ids


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Manage Raspberry Pi ET312 devices")
    parser.add_argument(
        "--install-dir",
        default=str(DEFAULT_INSTALL_DIR),
        help=f"Bridge install directory. Default: {DEFAULT_INSTALL_DIR}",
    )
    parser.add_argument(
        "--systemd-dir",
        default=str(DEFAULT_SYSTEMD_DIR),
        help=f"Systemd unit directory. Default: {DEFAULT_SYSTEMD_DIR}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("ensure-layout", help="Create the shared config layout")
    subparsers.add_parser("migrate-legacy-config", help="Migrate legacy single-device config")
    subparsers.add_parser("generate-units", help="Generate per-device systemd units")
    subparsers.add_parser("list-device-ids", help="Print enabled device ids")
    subparsers.add_parser("next-rfcomm-device", help="Print the next unused /dev/rfcommN path")

    register_serial = subparsers.add_parser("register-serial", help="Register one serial device")
    register_serial.add_argument("--device", required=True)
    register_serial.add_argument("--device-id")

    register_bt = subparsers.add_parser("register-bluetooth", help="Register one Bluetooth device")
    register_bt.add_argument("--mac", required=True)
    register_bt.add_argument("--rfcomm-device", required=True)
    register_bt.add_argument("--rfcomm-channel", required=True)
    register_bt.add_argument("--bluetooth-name")
    register_bt.add_argument("--device-id")

    discover_bt = subparsers.add_parser(
        "discover-bluetooth",
        help="Scan, pair, interrogate, and register ET312 Bluetooth devices",
    )
    discover_bt.add_argument("--scan-seconds", type=int)
    discover_bt.add_argument("--name-patterns")

    return parser.parse_args()


def main() -> None:
    """Run the requested manager command."""
    args = parse_args()
    install_dir = Path(args.install_dir)
    systemd_dir = Path(args.systemd_dir)

    if args.command == "ensure-layout":
        ensure_layout(install_dir)
        return

    if args.command == "migrate-legacy-config":
        ensure_layout(install_dir)
        for device_id in migrate_legacy_config(install_dir):
            print(device_id)
        return

    if args.command == "register-serial":
        device_id = register_serial_device(
            install_dir,
            device=args.device,
            device_id=args.device_id,
        )
        print(device_id)
        return

    if args.command == "register-bluetooth":
        device_id = register_bluetooth_device(
            install_dir,
            mac=args.mac,
            rfcomm_device=args.rfcomm_device,
            rfcomm_channel=args.rfcomm_channel,
            bluetooth_name=args.bluetooth_name,
            device_id=args.device_id,
        )
        print(device_id)
        return

    if args.command == "discover-bluetooth":
        ensure_layout(install_dir)
        discovery_config = parse_env_file(install_paths(install_dir)["discovery_config"])
        patterns = parse_patterns(
            args.name_patterns
            or discovery_config.get("DISCOVERY_NAME_PATTERNS", ",".join(DEFAULT_DISCOVERY_PATTERNS))
        )
        scan_seconds = args.scan_seconds or int(
            discovery_config.get("DISCOVERY_SCAN_SECONDS", str(DEFAULT_DISCOVERY_SCAN_SECONDS))
        )
        for device_id in discover_bluetooth_devices(
            install_dir,
            scan_seconds=scan_seconds,
            name_patterns=patterns,
        ):
            print(device_id)
        return

    if args.command == "generate-units":
        ensure_layout(install_dir)
        for unit_name in generate_units(install_dir, systemd_dir):
            print(unit_name)
        return

    if args.command == "list-device-ids":
        for device in load_enabled_devices(install_dir):
            print(device["DEVICE_ID"])
        return

    if args.command == "next-rfcomm-device":
        print(next_rfcomm_device(install_dir))
        return

    raise RuntimeError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    main()
