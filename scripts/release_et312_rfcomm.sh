#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/config-file" >&2
  exit 1
fi

CONFIG_FILE="$1"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  exit 0
fi

set -a
source "${CONFIG_FILE}"
set +a

RFCOMM_DEVICE="${RFCOMM_DEVICE:-/dev/rfcomm0}"
RFCOMM_ID="${RFCOMM_DEVICE#/dev/rfcomm}"

if [[ "${RFCOMM_ID}" =~ ^[0-9]+$ ]]; then
  exec rfcomm release "${RFCOMM_ID}"
fi

exit 0
