"""MQTT payload helpers for ET312."""

from __future__ import annotations


def payload_to_text(payload: object) -> str:
    """Return an MQTT payload as normalized text."""
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="ignore")
    return str(payload)
