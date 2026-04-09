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
EFFECTIVE_STARTUP_DELAY="${STARTUP_DELAY:-1.5}"
EFFECTIVE_CONNECT_RETRIES="${CONNECT_RETRIES:-4}"
EFFECTIVE_RECONNECT_DELAY="${RECONNECT_DELAY:-2.0}"

if [[ "${DEVICE}" == /dev/rfcomm* ]]; then
  if awk "BEGIN { exit !(${EFFECTIVE_STARTUP_DELAY} < 2.0) }"; then
    EFFECTIVE_STARTUP_DELAY="2.0"
  fi

  if [[ "${EFFECTIVE_CONNECT_RETRIES}" =~ ^[0-9]+$ ]] && (( EFFECTIVE_CONNECT_RETRIES < 4 )); then
    EFFECTIVE_CONNECT_RETRIES="4"
  fi

  if awk "BEGIN { exit !(${EFFECTIVE_RECONNECT_DELAY} < 3.0) }"; then
    EFFECTIVE_RECONNECT_DELAY="3.0"
  fi
fi

for _ in $(seq 1 20); do
  if [[ -e "${DEVICE}" ]]; then
    break
  fi
  sleep 1
done

if [[ ! -e "${DEVICE}" ]]; then
  echo "Serial device not found: ${DEVICE}" >&2
  exit 1
fi

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
  --startup-delay "${EFFECTIVE_STARTUP_DELAY}"
  --sync-attempts "${SYNC_ATTEMPTS:-40}"
  --sync-read-timeout "${SYNC_READ_TIMEOUT:-0.35}"
  --sync-inter-attempt-delay "${SYNC_INTER_ATTEMPT_DELAY:-0.1}"
  --post-sync-delay "${POST_SYNC_DELAY:-0.2}"
  --key-exchange-timeout "${KEY_EXCHANGE_TIMEOUT:-1.5}"
  --connect-retries "${EFFECTIVE_CONNECT_RETRIES}"
  --reconnect-delay "${EFFECTIVE_RECONNECT_DELAY}"
)

if [[ -n "${MQTT_USERNAME:-}" ]]; then
  ARGS+=(--username "${MQTT_USERNAME}")
fi

if [[ -n "${MQTT_PASSWORD:-}" ]]; then
  ARGS+=(--password "${MQTT_PASSWORD}")
fi

exec "${SCRIPT_DIR}/../.venv/bin/python" "${ARGS[@]}"
