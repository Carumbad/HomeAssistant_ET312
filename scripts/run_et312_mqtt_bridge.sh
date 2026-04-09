#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/config-file" >&2
  exit 1
fi

CONFIG_FILE="$1"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "Config file not found: ${CONFIG_FILE}" >&2
  exit 1
fi

set -a
source "${CONFIG_FILE}"
set +a

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ARGS=(
  "${SCRIPT_DIR}/et312_mqtt_bridge.py"
  "${DEVICE}"
  --baudrate "${BAUDRATE}"
  --timeout "${TIMEOUT}"
  --mqtt-host "${MQTT_HOST}"
  --mqtt-port "${MQTT_PORT}"
  --state-topic "${STATE_TOPIC}"
  --command-topic "${COMMAND_TOPIC}"
  --availability-topic "${AVAILABILITY_TOPIC}"
  --poll-interval "${POLL_INTERVAL}"
)

if [[ -n "${MQTT_USERNAME:-}" ]]; then
  ARGS+=(--username "${MQTT_USERNAME}")
fi

if [[ -n "${MQTT_PASSWORD:-}" ]]; then
  ARGS+=(--password "${MQTT_PASSWORD}")
fi

exec "${SCRIPT_DIR}/../.venv/bin/python" "${ARGS[@]}"
