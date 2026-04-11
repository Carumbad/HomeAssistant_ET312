"""MQTT payload helpers for ET312."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .topics import normalize_device_id


def payload_to_text(payload: object) -> str:
    """Return an MQTT payload as normalized text."""
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="ignore")
    return str(payload)


def command_payload_for_device(
    device_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a command payload tagged with the target ET312 device id."""
    command_payload = dict(payload)
    command_payload["device_id"] = normalize_device_id(device_id)
    return command_payload
