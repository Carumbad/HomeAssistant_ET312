"""Helpers for ET312 MQTT topic paths and stable device identifiers."""

from __future__ import annotations

import re
from typing import Mapping

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_DEVICE,
    CONF_DEVICE_ID,
    CONF_MQTT_STATE_TOPIC,
    CONNECTION_MQTT,
)

DEVICE_ID_RE = re.compile(r"^ET312_[0-9A-F]{6}$")
STATE_TOPIC_RE = re.compile(r"^(?P<prefix>.+)/(?P<device_id>ET312_[0-9A-F]{6})/state$")


def normalize_device_id(value: str) -> str:
    """Normalize a user-supplied ET312 device id."""
    return value.strip().upper()


def is_valid_device_id(value: str) -> bool:
    """Return whether a value matches the ET312 device-id format."""
    return DEVICE_ID_RE.fullmatch(normalize_device_id(value)) is not None


def build_topics(device_id: str, topic_prefix: str) -> dict[str, str]:
    """Build ET312 topic paths from a device id and topic prefix."""
    prefix = topic_prefix.strip().strip("/")
    normalized_id = normalize_device_id(device_id)
    return {
        "state": f"{prefix}/{normalized_id}/state",
        "command": f"{prefix}/{normalized_id}/command",
        "availability": f"{prefix}/{normalized_id}/availability",
    }


def extract_device_id_from_state_topic(state_topic: str) -> str | None:
    """Extract ET312 device id from an MQTT state topic path."""
    topic = state_topic.strip().strip("/")
    match = STATE_TOPIC_RE.fullmatch(topic)
    if not match:
        return None
    return normalize_device_id(match.group("device_id"))


def extract_prefix_from_state_topic(state_topic: str) -> str | None:
    """Extract topic prefix from an ET312 MQTT state topic path."""
    topic = state_topic.strip().strip("/")
    match = STATE_TOPIC_RE.fullmatch(topic)
    if not match:
        return None
    return match.group("prefix")


def resolve_bridge_device_id(device_id: str | None, state_topic: str) -> str:
    """Resolve a bridge device id from explicit config or the MQTT state topic."""
    if device_id and device_id.strip():
        return normalize_device_id(device_id)
    return extract_device_id_from_state_topic(state_topic) or ""


def entry_device_id(data: Mapping[str, object]) -> str:
    """Resolve a stable per-device identifier from config-entry data."""
    explicit_device_id = data.get(CONF_DEVICE_ID)
    if isinstance(explicit_device_id, str) and explicit_device_id.strip():
        return normalize_device_id(explicit_device_id)

    if data.get(CONF_CONNECTION_TYPE) == CONNECTION_MQTT:
        state_topic = data.get(CONF_MQTT_STATE_TOPIC)
        if isinstance(state_topic, str):
            inferred = extract_device_id_from_state_topic(state_topic)
            if inferred:
                return inferred

    if data.get(CONF_CONNECTION_TYPE) == CONNECTION_MQTT:
        return "ET312_MQTT"

    serial_path = str(data.get(CONF_DEVICE, "")).strip() or "unknown_serial"
    slug = "".join(ch if ch.isalnum() else "_" for ch in serial_path).strip("_")
    return f"ET312_SERIAL_{slug.upper()}"
