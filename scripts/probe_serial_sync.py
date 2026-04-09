"""Low-level serial probe for ET312 sync debugging."""

from __future__ import annotations

import argparse
import time

import serial


def probe(device: str, baudrate: int, settle: float) -> None:
    """Send raw sync bytes and print what comes back."""
    print(f"\nProbing {device} at {baudrate} baud")
    port = serial.Serial(
        device,
        baudrate,
        timeout=0.2,
        write_timeout=0.2,
        parity=serial.PARITY_NONE,
        bytesize=serial.EIGHTBITS,
        stopbits=serial.STOPBITS_ONE,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )

    try:
        if settle:
            time.sleep(settle)

        try:
            port.reset_input_buffer()
            port.reset_output_buffer()
        except Exception:
            pass

        for idx in range(12):
            written = port.write(b"\x00")
            port.flush()
            response = port.read(1)
            print(
                f"  try={idx + 1:02d} wrote={written} "
                f"resp={response.hex() if response else '<none>'}"
            )
            if response == b"\x07":
                print("  sync marker received")
                break
    finally:
        port.close()


def main() -> None:
    """Run the probe."""
    parser = argparse.ArgumentParser(description="Probe ET312 serial sync")
    parser.add_argument("device")
    parser.add_argument("--settle", type=float, default=0.5)
    args = parser.parse_args()

    for baudrate in (19200, 38400):
        probe(args.device, baudrate, args.settle)


if __name__ == "__main__":
    main()
