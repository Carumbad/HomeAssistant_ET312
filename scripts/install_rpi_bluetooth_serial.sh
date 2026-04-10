#!/usr/bin/env bash

set -euo pipefail

INSTALL_DIR="/opt/et312-mqtt-bridge"
SYSTEMD_DIR="/etc/systemd/system"
SERVICE_USER="et312"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DISCOVER="0"
ET312_MAC=""
RFCOMM_DEVICE=""
RFCOMM_CHANNEL=""
SCAN_SECONDS=""
NAME_PATTERNS=""
DEVICE_ID=""
BLUETOOTH_NAME=""

usage() {
  cat <<EOF
Usage:
  sudo ./scripts/install_rpi_bluetooth_serial.sh --discover [options]
  sudo ./scripts/install_rpi_bluetooth_serial.sh --mac AA:BB:CC:DD:EE:FF [options]

Options:
  --discover                 Scan, pair, interrogate, and register matching ET312s.
  --mac MAC                  Bluetooth MAC address for one ET312 device.
  --rfcomm-device PATH       Serial mapping to create, e.g. /dev/rfcomm0.
  --rfcomm-channel CHANNEL   RFCOMM channel. Defaults to SDP autodetect.
  --scan-seconds SECONDS     Discovery scan duration. Uses shared config if omitted.
  --name-patterns CSV        Discovery name fragments, e.g. Micro,312.
  --device-id ID             Optional stable id override for --mac mode.
  --bluetooth-name NAME      Optional friendly name override for --mac mode.
  --install-dir PATH         App install location. Default: ${INSTALL_DIR}
  --service-user USER        Service account. Default: ${SERVICE_USER}
  --help                     Show this help.

Examples:
  sudo ./scripts/install_rpi_bluetooth_serial.sh --discover
  sudo ./scripts/install_rpi_bluetooth_serial.sh --mac AA:BB:CC:DD:EE:FF --rfcomm-device /dev/rfcomm0
EOF
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Please run this installer with sudo or as root." >&2
    exit 1
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --discover)
        DISCOVER="1"
        shift
        ;;
      --mac)
        ET312_MAC="$2"
        shift 2
        ;;
      --rfcomm-device)
        RFCOMM_DEVICE="$2"
        shift 2
        ;;
      --rfcomm-channel)
        RFCOMM_CHANNEL="$2"
        shift 2
        ;;
      --scan-seconds)
        SCAN_SECONDS="$2"
        shift 2
        ;;
      --name-patterns)
        NAME_PATTERNS="$2"
        shift 2
        ;;
      --device-id)
        DEVICE_ID="$2"
        shift 2
        ;;
      --bluetooth-name)
        BLUETOOTH_NAME="$2"
        shift 2
        ;;
      --install-dir)
        INSTALL_DIR="$2"
        shift 2
        ;;
      --service-user)
        SERVICE_USER="$2"
        shift 2
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage >&2
        exit 1
        ;;
    esac
  done

  if [[ "${DISCOVER}" != "1" && -z "${ET312_MAC}" ]]; then
    echo "Use --discover or provide --mac." >&2
    usage >&2
    exit 1
  fi
}

install_system_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y bluez bluez-tools rfkill rsync python3 python3-venv python3-pip
}

ensure_service_user() {
  if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/var/lib/et312" --shell /usr/sbin/nologin "${SERVICE_USER}"
  fi

  usermod -a -G dialout "${SERVICE_USER}" || true
}

install_app_files() {
  mkdir -p "${INSTALL_DIR}"

  rsync -a --delete \
    --exclude '.git/' \
    --exclude '.githooks/' \
    --exclude '.venv/' \
    --exclude 'References/' \
    --exclude 'config/' \
    --exclude '__pycache__/' \
    --exclude '.DS_Store' \
    "${REPO_ROOT}/" "${INSTALL_DIR}/"

  if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
    python3 -m venv "${INSTALL_DIR}/.venv"
  fi

  if ! "${INSTALL_DIR}/.venv/bin/python" -c 'import paho.mqtt.client, serial' >/dev/null 2>&1; then
    "${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
    "${INSTALL_DIR}/.venv/bin/pip" install pyserial paho-mqtt
  fi

  find "${INSTALL_DIR}/scripts" -maxdepth 1 -type f -name '*.sh' -exec chmod 0755 {} +
  chmod 0755 "${INSTALL_DIR}/scripts/et312_rpi_manager.py"
  chown -R root:root "${INSTALL_DIR}"
}

prepare_bluetooth() {
  rfkill unblock bluetooth || true
  systemctl enable --now bluetooth
  bluetoothctl power on >/dev/null 2>&1 || true
  bluetoothctl agent on >/dev/null 2>&1 || true
  bluetoothctl default-agent >/dev/null 2>&1 || true
}

initialize_layout() {
  "${INSTALL_DIR}/.venv/bin/python" \
    "${INSTALL_DIR}/scripts/et312_rpi_manager.py" \
    --install-dir "${INSTALL_DIR}" \
    ensure-layout

  "${INSTALL_DIR}/.venv/bin/python" \
    "${INSTALL_DIR}/scripts/et312_rpi_manager.py" \
    --install-dir "${INSTALL_DIR}" \
    migrate-legacy-config >/dev/null || true

  chown root:"${SERVICE_USER}" "${INSTALL_DIR}/config"
  chmod 0750 "${INSTALL_DIR}/config"
  if [[ -d "${INSTALL_DIR}/config/devices" ]]; then
    chown root:"${SERVICE_USER}" "${INSTALL_DIR}/config/devices"
    chmod 0750 "${INSTALL_DIR}/config/devices"
  fi
}

fix_config_permissions() {
  chown root:"${SERVICE_USER}" "${INSTALL_DIR}/config"
  chmod 0750 "${INSTALL_DIR}/config"
  if [[ -d "${INSTALL_DIR}/config/devices" ]]; then
    chown root:"${SERVICE_USER}" "${INSTALL_DIR}/config/devices"
    chmod 0750 "${INSTALL_DIR}/config/devices"
    find "${INSTALL_DIR}/config/devices" -maxdepth 1 -type f -exec chown root:"${SERVICE_USER}" {} +
    find "${INSTALL_DIR}/config/devices" -maxdepth 1 -type f -exec chmod 0640 {} +
  fi
}

register_one_device() {
  local channel="${RFCOMM_CHANNEL}"
  local rfcomm_device="${RFCOMM_DEVICE}"
  local args

  bluetoothctl pair "${ET312_MAC}" >/dev/null 2>&1 || true
  bluetoothctl trust "${ET312_MAC}" >/dev/null 2>&1 || true

  if [[ -z "${channel}" ]]; then
    channel="$(sdptool search --bdaddr "${ET312_MAC}" SP 2>/dev/null | awk '/Channel:/ {print $2; exit}')"
    channel="${channel:-2}"
  fi

  if [[ -z "${rfcomm_device}" ]]; then
    rfcomm_device="$("${INSTALL_DIR}/.venv/bin/python" \
      "${INSTALL_DIR}/scripts/et312_rpi_manager.py" \
      --install-dir "${INSTALL_DIR}" \
      next-rfcomm-device)"
  fi

  args=(
    "${INSTALL_DIR}/scripts/et312_rpi_manager.py"
    --install-dir "${INSTALL_DIR}"
    register-bluetooth
    --mac "${ET312_MAC}"
    --rfcomm-device "${rfcomm_device}"
    --rfcomm-channel "${channel}"
  )

  if [[ -n "${BLUETOOTH_NAME}" ]]; then
    args+=(--bluetooth-name "${BLUETOOTH_NAME}")
  fi

  if [[ -n "${DEVICE_ID}" ]]; then
    args+=(--device-id "${DEVICE_ID}")
  fi

  "${INSTALL_DIR}/.venv/bin/python" "${args[@]}"
}

run_discovery() {
  local args=(
    "${INSTALL_DIR}/scripts/et312_rpi_manager.py"
    --install-dir "${INSTALL_DIR}"
    discover-bluetooth
  )

  if [[ -n "${SCAN_SECONDS}" ]]; then
    args+=(--scan-seconds "${SCAN_SECONDS}")
  fi

  if [[ -n "${NAME_PATTERNS}" ]]; then
    args+=(--name-patterns "${NAME_PATTERNS}")
  fi

  "${INSTALL_DIR}/.venv/bin/python" "${args[@]}"
}

generate_units() {
  "${INSTALL_DIR}/.venv/bin/python" \
    "${INSTALL_DIR}/scripts/et312_rpi_manager.py" \
    --install-dir "${INSTALL_DIR}" \
    --systemd-dir "${SYSTEMD_DIR}" \
    migrate-legacy-config >/dev/null || true

  "${INSTALL_DIR}/.venv/bin/python" \
    "${INSTALL_DIR}/scripts/et312_rpi_manager.py" \
    --install-dir "${INSTALL_DIR}" \
    --systemd-dir "${SYSTEMD_DIR}" \
    generate-units
}

enable_and_start_units() {
  local units="$1"
  systemctl daemon-reload

  if [[ -z "${units}" ]]; then
    return
  fi

  while IFS= read -r unit_name; do
    [[ -z "${unit_name}" ]] && continue
    systemctl enable "${unit_name}" >/dev/null
  done <<< "${units}"

  while IFS= read -r unit_name; do
    [[ -z "${unit_name}" ]] && continue
    if [[ "${unit_name}" == et312-rfcomm-* ]]; then
      systemctl restart "${unit_name}"
    fi
  done <<< "${units}"

  while IFS= read -r unit_name; do
    [[ -z "${unit_name}" ]] && continue
    if [[ "${unit_name}" == et312-mqtt-bridge-* ]]; then
      systemctl restart "${unit_name}"
    fi
  done <<< "${units}"
}

print_summary() {
  local device_ids
  device_ids="$("${INSTALL_DIR}/.venv/bin/python" \
    "${INSTALL_DIR}/scripts/et312_rpi_manager.py" \
    --install-dir "${INSTALL_DIR}" \
    list-device-ids || true)"

  cat <<EOF

ET312 Bluetooth support installed.

Configured device ids:
${device_ids:-  <none>}

Useful commands:
  sudo systemctl list-units 'et312-*'
  sudo journalctl -u 'et312-rfcomm-*' -u 'et312-mqtt-bridge-*' -f
  sudo rfcomm
  ls -l /dev/rfcomm*
EOF
}

main() {
  require_root
  parse_args "$@"
  install_system_packages
  ensure_service_user
  install_app_files
  initialize_layout
  prepare_bluetooth

  if [[ "${DISCOVER}" == "1" ]]; then
    run_discovery >/dev/null
  else
    register_one_device >/dev/null
  fi

  fix_config_permissions
  generated_units="$(generate_units)"
  enable_and_start_units "${generated_units}"
  print_summary
}

main "$@"
