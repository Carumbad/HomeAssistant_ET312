#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="et312-rfcomm"
INSTALL_DIR="/opt/et312-mqtt-bridge"
CONFIG_FILE="${INSTALL_DIR}/config/${SERVICE_NAME}.env"
SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
BRIDGE_SERVICE_USER="et312"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ET312_MAC=""
RFCOMM_DEVICE="/dev/rfcomm0"
RFCOMM_CHANNEL=""
SCAN_SECONDS="15"

usage() {
  cat <<EOF
Usage:
  sudo ./scripts/install_rpi_bluetooth_serial.sh --mac AA:BB:CC:DD:EE:FF [options]

Options:
  --mac MAC                 Bluetooth MAC address of the ET312 Audio device. Required.
  --rfcomm-device PATH      Serial mapping to create. Default: ${RFCOMM_DEVICE}
  --rfcomm-channel CHANNEL  RFCOMM channel. Defaults to SDP autodetect, usually 2.
  --scan-seconds SECONDS    Discovery scan duration before pairing. Default: ${SCAN_SECONDS}
  --help                    Show this help.

Example:
  sudo ./scripts/install_rpi_bluetooth_serial.sh \\
    --mac AA:BB:CC:DD:EE:FF \\
    --rfcomm-device /dev/rfcomm0
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

  if [[ -z "${ET312_MAC}" ]]; then
    echo "--mac is required." >&2
    usage >&2
    exit 1
  fi
}

install_system_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y bluez bluez-tools rfkill rsync
}

sync_launcher_script() {
  mkdir -p "${INSTALL_DIR}"
  mkdir -p "${INSTALL_DIR}/scripts"

  rsync -a \
    --exclude '.git/' \
    --exclude '.githooks/' \
    --exclude 'References/' \
    --exclude '__pycache__/' \
    --exclude '.DS_Store' \
    "${REPO_ROOT}/scripts/run_et312_rfcomm.sh" \
    "${REPO_ROOT}/scripts/release_et312_rfcomm.sh" \
    "${INSTALL_DIR}/scripts/"

  chmod 0755 "${INSTALL_DIR}/scripts/run_et312_rfcomm.sh"
  chmod 0755 "${INSTALL_DIR}/scripts/release_et312_rfcomm.sh"
}

prepare_bluetooth() {
  rfkill unblock bluetooth || true
  systemctl enable --now bluetooth
}

detect_rfcomm_channel() {
  if [[ -n "${RFCOMM_CHANNEL}" ]]; then
    return
  fi

  local detected
  detected="$(sdptool search --bdaddr "${ET312_MAC}" SP 2>/dev/null | awk '/Channel:/ {print $2; exit}')"

  if [[ -n "${detected}" ]]; then
    RFCOMM_CHANNEL="${detected}"
    return
  fi

  RFCOMM_CHANNEL="2"
}

pair_et312() {
  echo "Make sure the ET312 Bluetooth interface is powered and discoverable."
  echo "Scanning for ${SCAN_SECONDS} seconds before pairing ${ET312_MAC}..."

  bluetoothctl <<EOF
power on
agent on
default-agent
scan on
EOF

  sleep "${SCAN_SECONDS}"

  bluetoothctl <<EOF
scan off
pair ${ET312_MAC}
trust ${ET312_MAC}
info ${ET312_MAC}
quit
EOF
}

write_config() {
  local config_group="root"

  if getent group "${BRIDGE_SERVICE_USER}" >/dev/null 2>&1; then
    config_group="${BRIDGE_SERVICE_USER}"
  fi

  install -m 0750 -o root -g "${config_group}" -d "$(dirname "${CONFIG_FILE}")"

  cat > "${CONFIG_FILE}" <<EOF
ET312_BLUETOOTH_MAC="${ET312_MAC}"
RFCOMM_DEVICE="${RFCOMM_DEVICE}"
RFCOMM_CHANNEL="${RFCOMM_CHANNEL}"
EOF

  chown root:"${config_group}" "${CONFIG_FILE}"
  chmod 0644 "${CONFIG_FILE}"
}

write_systemd_unit() {
  cat > "${SYSTEMD_UNIT}" <<EOF
[Unit]
Description=ET312 Bluetooth RFCOMM Binding
After=bluetooth.service network.target
Requires=bluetooth.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=${INSTALL_DIR}/scripts/run_et312_rfcomm.sh ${CONFIG_FILE}
ExecStop=${INSTALL_DIR}/scripts/release_et312_rfcomm.sh ${CONFIG_FILE}

[Install]
WantedBy=multi-user.target
EOF
}

enable_service() {
  systemctl daemon-reload
  systemctl enable --now "${SERVICE_NAME}"
}

print_summary() {
  cat <<EOF

ET312 Bluetooth serial mapping installed.

Expected serial device:
  ${RFCOMM_DEVICE}

Selected RFCOMM channel:
  ${RFCOMM_CHANNEL}

Useful commands:
  sudo systemctl status ${SERVICE_NAME}
  sudo journalctl -u ${SERVICE_NAME} -f
  sudo rfcomm
  ls -l ${RFCOMM_DEVICE}

If the Bluetooth mapping works, you can point the MQTT bridge at:
  ${RFCOMM_DEVICE}
EOF
}

main() {
  require_root
  parse_args "$@"
  install_system_packages
  sync_launcher_script
  prepare_bluetooth
  pair_et312
  detect_rfcomm_channel
  write_config
  write_systemd_unit
  enable_service
  print_summary
}

main "$@"
