"""Constants for the ET312 integration."""

from __future__ import annotations

DOMAIN = "et312"
DEFAULT_NAME = "ET312"
DEFAULT_TIMEOUT = 10.0
DEFAULT_BAUDRATE = 19200
DEFAULT_SCAN_INTERVAL_SECONDS = 5
DEFAULT_MQTT_STATE_TOPIC = "et312/state"
DEFAULT_MQTT_COMMAND_TOPIC = "et312/command"
DEFAULT_MQTT_AVAILABILITY_TOPIC = "et312/availability"

CONF_BAUDRATE = "baudrate"
CONF_CONNECTION_TYPE = "connection_type"
CONF_DEVICE = "device"
CONF_MQTT_AVAILABILITY_TOPIC = "mqtt_availability_topic"
CONF_MQTT_COMMAND_TOPIC = "mqtt_command_topic"
CONF_MQTT_STATE_TOPIC = "mqtt_state_topic"
CONF_TIMEOUT = "timeout"

CONNECTION_SERIAL = "serial"
CONNECTION_MQTT = "mqtt"

REG_CURRENT_MODE = 0x407B
REG_EXECUTE_COMMAND = 0x4070
REG_CONTROL_FLAGS = 0x400F
REG_CHANNEL_A_LEVEL = 0x4064
REG_CHANNEL_B_LEVEL = 0x4065
REG_BATTERY_PERCENT = 0x4203
REG_MULTI_ADJUST_VALUE = 0x420D
REG_CIPHER_KEY = 0x4213

CONTROL_FLAG_DISABLE_KNOBS = 0x01
CONTROL_FLAG_DISABLE_MULTI_ADJUST = 0x08

CHANNEL_A_BASE = 0x4000
CHANNEL_B_BASE = 0x4100

CHANNEL_POWER_MIN = 0x80
CHANNEL_POWER_MAX = 0xFF
CHANNEL_POWER_UI_MIN = 0
CHANNEL_POWER_UI_MAX = 99
MULTI_ADJUST_UI_MIN = 0
MULTI_ADJUST_UI_MAX = 99

MODES: dict[int, str] = {
    0x00: "Power On",
    0x6B: "Low",
    0x6C: "Normal",
    0x6D: "High",
    0x76: "Waves",
    0x77: "Stroke",
    0x78: "Climb",
    0x79: "Combo",
    0x7A: "Intense",
    0x7B: "Rhythm",
    0x7C: "Audio 1",
    0x7D: "Audio 2",
    0x7E: "Audio 3",
    0x7F: "Split",
    0x80: "Random 1",
    0x81: "Random 2",
    0x82: "Toggle",
    0x83: "Orgasm",
    0x84: "Torment",
    0x85: "Phase 1",
    0x86: "Phase 2",
    0x87: "Phase 3",
    0x88: "User 1",
    0x89: "User 2",
    0x8A: "User 3",
    0x8B: "User 4",
    0x8C: "User 5",
    0x8D: "User 6",
    0x8E: "User 7",
}
