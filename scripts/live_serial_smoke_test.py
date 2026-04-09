"""Minimal live ET312 serial smoke test."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.et312.const import CONNECTION_SERIAL
from custom_components.et312.et312 import (
    ET312Client,
    ET312ConnectionConfig,
    ET312ConnectionError,
    apply_cipher,
    build_read_command,
    build_write_command,
    calculate_checksum,
    decode_read_response,
    raw_byte_to_ui_99,
    raw_level_byte_to_ui_99,
)


def _read_exact(port, length: int, timeout: float) -> bytes:
    """Read exactly `length` bytes or return fewer if timed out."""
    old_timeout = port.timeout
    port.timeout = timeout
    try:
        return port.read(length)
    finally:
        port.timeout = old_timeout


def _blocking_sync(port, key: int | None) -> None:
    """Synchronize a blocking serial connection with the ET312."""
    for _ in range(12):
        payload = bytes(apply_cipher([0x00], key))
        port.write(payload)
        response = _read_exact(port, 1, 0.1)
        if not response:
            continue
        if response[0] != 0x07:
            raise ET312ConnectionError(
                f"Unexpected ET312 sync response: 0x{response[0]:02X}"
            )
        return
    raise ET312ConnectionError("ET312 synchronisation failed")


def _blocking_setup_key(port) -> int:
    """Negotiate the ET312 outbound XOR key using blocking serial I/O."""
    command = [0x2F, 0x00]
    payload = command + [calculate_checksum(command)]
    port.write(bytes(payload))
    response = _read_exact(port, 3, 1.0)
    if len(response) != 3:
        raise ET312ConnectionError("Timeout during ET312 key setup")
    if calculate_checksum(list(response[:-1])) != response[-1]:
        raise ET312ConnectionError("ET312 key setup checksum mismatch")
    if response[0] != 0x21:
        raise ET312ConnectionError(
            f"Unexpected ET312 key setup response: 0x{response[0]:02X}"
        )
    return response[1]


def _blocking_read_register(port, address: int, key: int) -> int:
    """Read a single ET312 register using blocking serial I/O."""
    port.write(bytes(apply_cipher(build_read_command(address), key)))
    response = _read_exact(port, 3, 1.0)
    if len(response) != 3:
        raise ET312ConnectionError(f"Timed out reading register 0x{address:04X}")
    return decode_read_response(list(response))


def _run_blocking_read_only(device: str, baudrate: int, timeout: float) -> None:
    """Fallback smoke test using plain blocking pyserial."""
    import serial

    port = serial.Serial(
        device,
        baudrate,
        timeout=timeout,
        parity=serial.PARITY_NONE,
        bytesize=serial.EIGHTBITS,
        stopbits=serial.STOPBITS_ONE,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )

    try:
        port.reset_input_buffer()
        _blocking_sync(port, None)
        device_key = _blocking_setup_key(port)
        cipher_key = device_key ^ 0x55

        mode_code = _blocking_read_register(port, 0x407B, cipher_key)
        level_a = _blocking_read_register(port, 0x4064, cipher_key)
        level_b = _blocking_read_register(port, 0x4065, cipher_key)
        battery = _blocking_read_register(port, 0x4203, cipher_key)
        ma = _blocking_read_register(port, 0x420D, cipher_key)

        print("Connected to ET312 via blocking fallback")
        print(
            "State:"
            f" mode_code=0x{mode_code:02X},"
            f" power_level_a={raw_level_byte_to_ui_99(level_a)},"
            f" power_level_b={raw_level_byte_to_ui_99(level_b)},"
            f" battery_percent={raw_byte_to_ui_99(battery)},"
            f" multi_adjust={raw_byte_to_ui_99(ma)}"
        )

        reset_payload = bytes(apply_cipher(build_write_command(0x4213, [0x00]), cipher_key))
        port.write(reset_payload)
        _read_exact(port, 1, 1.0)
    finally:
        port.close()


async def main() -> None:
    """Run a focused ET312 hardware smoke test."""
    parser = argparse.ArgumentParser(description="ET312 serial smoke test")
    parser.add_argument("device", help="Serial device path, for example /dev/ttyUSB0")
    parser.add_argument("--baudrate", type=int, default=19200)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--mode", help="Optional ET312 mode to switch to")
    parser.add_argument("--power-a", type=int, help="Optional channel A power (0-99)")
    parser.add_argument("--power-b", type=int, help="Optional channel B power (0-99)")
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Connect and read state without writing any settings",
    )
    parser.add_argument(
        "--blocking",
        action="store_true",
        help="Use plain blocking pyserial instead of the async transport",
    )
    args = parser.parse_args()

    if args.blocking:
        _run_blocking_read_only(args.device, args.baudrate, args.timeout)
        return

    client = ET312Client(
        ET312ConnectionConfig(
            connection_type=CONNECTION_SERIAL,
            device=args.device,
            baudrate=args.baudrate,
            timeout=args.timeout,
        )
    )

    try:
        await client.async_connect()
        before = await client.async_get_state()
        print("Connected to ET312")
        print(f"Initial state: {before}")

        if not args.read_only:
            if args.mode:
                print(f"Switching mode to: {args.mode}")
                await client.async_set_mode(args.mode)

            if args.power_a is not None:
                print(f"Setting channel A power to: {args.power_a}")
                await client.async_set_channel_power("a", args.power_a)

            if args.power_b is not None:
                print(f"Setting channel B power to: {args.power_b}")
                await client.async_set_channel_power("b", args.power_b)

        after = await client.async_get_state()
        print(f"Final state: {after}")
    except ET312ConnectionError as err:
        if not args.read_only:
            raise
        print(f"Async serial path failed: {err}")
        print("Trying blocking pyserial fallback...")
        _run_blocking_read_only(args.device, args.baudrate, args.timeout)
    finally:
        await client.async_disconnect()


if __name__ == "__main__":
    asyncio.run(main())
