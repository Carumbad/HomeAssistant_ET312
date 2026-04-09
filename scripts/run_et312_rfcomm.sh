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

: "${ET312_BLUETOOTH_MAC:?ET312_BLUETOOTH_MAC must be set}"

RFCOMM_DEVICE="${RFCOMM_DEVICE:-/dev/rfcomm0}"
RFCOMM_CHANNEL="${RFCOMM_CHANNEL:-1}"
RFCOMM_ID="${RFCOMM_DEVICE#/dev/rfcomm}"

if [[ ! "${RFCOMM_ID}" =~ ^[0-9]+$ ]]; then
  echo "RFCOMM_DEVICE must look like /dev/rfcomm0" >&2
  exit 1
fi

rfcomm release "${RFCOMM_ID}" >/dev/null 2>&1 || true
exec rfcomm bind "${RFCOMM_ID}" "${ET312_BLUETOOTH_MAC}" "${RFCOMM_CHANNEL}"
