#!/usr/bin/env bash

set -euo pipefail

SERVICE_USER="et312"
INSTALL_DIR="/opt/et312-mqtt-bridge"
SYSTEMD_DIR="/etc/systemd/system"
BRIDGE_CONFIG_FILE="${INSTALL_DIR}/config/et312-bridge.env"
DISCOVERY_CONFIG_FILE="${INSTALL_DIR}/config/et312-discovery.env"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DEVICE=""
DEVICE_ID=""
MQTT_HOST="127.0.0.1"
MQTT_PORT="1883"
MQTT_USERNAME=""
MQTT_PASSWORD=""
MQTT_TOPIC_PREFIX="et312"
POLL_INTERVAL="2.0"
TIMEOUT="1.0"
BAUDRATE="19200"
STARTUP_DELAY="2.0"
SYNC_ATTEMPTS="40"
SYNC_READ_TIMEOUT="0.35"
SYNC_INTER_ATTEMPT_DELAY="0.1"
POST_SYNC_DELAY="0.2"
KEY_EXCHANGE_TIMEOUT="1.5"
CONNECT_RETRIES="4"
RECONNECT_DELAY="3.0"
DISCOVERY_NAME_PATTERNS="Micro,312"
DISCOVERY_SCAN_SECONDS="20"

usage() {
  cat <<EOF
Usage:
  sudo ./scripts/install_rpi_bridge.sh [options]

Options:
  --device PATH                Optional serial device to register immediately.
  --device-id ID               Optional stable device id for --device.
  --mqtt-host HOST             MQTT broker host. Default: ${MQTT_HOST}
  --mqtt-port PORT             MQTT broker port. Default: ${MQTT_PORT}
  --mqtt-username USER         MQTT username.
  --mqtt-password PASS         MQTT password.
  --topic-prefix PREFIX        Base MQTT topic prefix. Default: ${MQTT_TOPIC_PREFIX}
  --poll-interval SECONDS      State publish interval. Default: ${POLL_INTERVAL}
  --timeout SECONDS            Serial timeout. Default: ${TIMEOUT}
  --baudrate BAUD              Serial baudrate. Default: ${BAUDRATE}
  --startup-delay SECONDS      Delay after opening the serial device. Default: ${STARTUP_DELAY}
  --sync-attempts COUNT        Sync byte attempts per connect. Default: ${SYNC_ATTEMPTS}
  --sync-read-timeout SEC      Per-sync read timeout. Default: ${SYNC_READ_TIMEOUT}
  --sync-gap SEC               Delay between sync attempts. Default: ${SYNC_INTER_ATTEMPT_DELAY}
  --post-sync-delay SEC        Delay after sync before key exchange. Default: ${POST_SYNC_DELAY}
  --key-timeout SEC            Key exchange timeout. Default: ${KEY_EXCHANGE_TIMEOUT}
  --connect-retries COUNT      Serial reconnect attempts. Default: ${CONNECT_RETRIES}
  --reconnect-delay SEC        Delay between reconnect attempts. Default: ${RECONNECT_DELAY}
  --discovery-name-patterns P  Comma-separated Bluetooth name fragments. Default: ${DISCOVERY_NAME_PATTERNS}
  --discovery-scan-seconds S   Bluetooth scan duration. Default: ${DISCOVERY_SCAN_SECONDS}
  --install-dir PATH           App install location. Default: ${INSTALL_DIR}
  --service-user USER          Service account. Default: ${SERVICE_USER}
  --help                       Show this help.

Examples:
  sudo ./scripts/install_rpi_bridge.sh --mqtt-host 192.168.1.20
  sudo ./scripts/install_rpi_bridge.sh --device /dev/ttyUSB0 --device-id ET312_USB_A
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
      --device)
        DEVICE="$2"
        shift 2
        ;;
      --device-id)
        DEVICE_ID="$2"
        shift 2
        ;;
      --mqtt-host)
        MQTT_HOST="$2"
        shift 2
        ;;
      --mqtt-port)
        MQTT_PORT="$2"
        shift 2
        ;;
      --mqtt-username)
        MQTT_USERNAME="$2"
        shift 2
        ;;
      --mqtt-password)
        MQTT_PASSWORD="$2"
        shift 2
        ;;
      --topic-prefix)
        MQTT_TOPIC_PREFIX="$2"
        shift 2
        ;;
      --poll-interval)
        POLL_INTERVAL="$2"
        shift 2
        ;;
      --timeout)
        TIMEOUT="$2"
        shift 2
        ;;
      --baudrate)
        BAUDRATE="$2"
        shift 2
        ;;
      --startup-delay)
        STARTUP_DELAY="$2"
        shift 2
        ;;
      --sync-attempts)
        SYNC_ATTEMPTS="$2"
        shift 2
        ;;
      --sync-read-timeout)
        SYNC_READ_TIMEOUT="$2"
        shift 2
        ;;
      --sync-gap)
        SYNC_INTER_ATTEMPT_DELAY="$2"
        shift 2
        ;;
      --post-sync-delay)
        POST_SYNC_DELAY="$2"
        shift 2
        ;;
      --key-timeout)
        KEY_EXCHANGE_TIMEOUT="$2"
        shift 2
        ;;
      --connect-retries)
        CONNECT_RETRIES="$2"
        shift 2
        ;;
      --reconnect-delay)
        RECONNECT_DELAY="$2"
        shift 2
        ;;
      --discovery-name-patterns)
        DISCOVERY_NAME_PATTERNS="$2"
        shift 2
        ;;
      --discovery-scan-seconds)
        DISCOVERY_SCAN_SECONDS="$2"
        shift 2
        ;;
      --install-dir)
        INSTALL_DIR="$2"
        BRIDGE_CONFIG_FILE="${INSTALL_DIR}/config/et312-bridge.env"
        DISCOVERY_CONFIG_FILE="${INSTALL_DIR}/config/et312-discovery.env"
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
}

install_system_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y python3 python3-venv python3-pip rsync
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

initialize_config_layout() {
  "${INSTALL_DIR}/.venv/bin/python" \
    "${INSTALL_DIR}/scripts/et312_rpi_manager.py" \
    --install-dir "${INSTALL_DIR}" \
    ensure-layout

  "${INSTALL_DIR}/.venv/bin/python" \
    "${INSTALL_DIR}/scripts/et312_rpi_manager.py" \
    --install-dir "${INSTALL_DIR}" \
    migrate-legacy-config >/dev/null || true

  cat > "${BRIDGE_CONFIG_FILE}" <<EOF
MQTT_HOST="${MQTT_HOST}"
MQTT_PORT="${MQTT_PORT}"
MQTT_USERNAME="${MQTT_USERNAME}"
MQTT_PASSWORD="${MQTT_PASSWORD}"
MQTT_TOPIC_PREFIX="${MQTT_TOPIC_PREFIX}"
POLL_INTERVAL="${POLL_INTERVAL}"
TIMEOUT="${TIMEOUT}"
BAUDRATE="${BAUDRATE}"
STARTUP_DELAY="${STARTUP_DELAY}"
SYNC_ATTEMPTS="${SYNC_ATTEMPTS}"
SYNC_READ_TIMEOUT="${SYNC_READ_TIMEOUT}"
SYNC_INTER_ATTEMPT_DELAY="${SYNC_INTER_ATTEMPT_DELAY}"
POST_SYNC_DELAY="${POST_SYNC_DELAY}"
KEY_EXCHANGE_TIMEOUT="${KEY_EXCHANGE_TIMEOUT}"
CONNECT_RETRIES="${CONNECT_RETRIES}"
RECONNECT_DELAY="${RECONNECT_DELAY}"
EOF

  cat > "${DISCOVERY_CONFIG_FILE}" <<EOF
DISCOVERY_NAME_PATTERNS="${DISCOVERY_NAME_PATTERNS}"
DISCOVERY_SCAN_SECONDS="${DISCOVERY_SCAN_SECONDS}"
EOF

  chown root:"${SERVICE_USER}" "${INSTALL_DIR}/config" "${BRIDGE_CONFIG_FILE}" "${DISCOVERY_CONFIG_FILE}"
  chmod 0750 "${INSTALL_DIR}/config"
  chmod 0640 "${BRIDGE_CONFIG_FILE}" "${DISCOVERY_CONFIG_FILE}"
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

register_initial_serial_device() {
  if [[ -z "${DEVICE}" ]]; then
    return
  fi

  local args=(
    "${INSTALL_DIR}/scripts/et312_rpi_manager.py"
    --install-dir "${INSTALL_DIR}"
    register-serial
    --device "${DEVICE}"
  )

  if [[ -n "${DEVICE_ID}" ]]; then
    args+=(--device-id "${DEVICE_ID}")
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

disable_legacy_units() {
  systemctl disable --now et312-rfcomm.service et312-mqtt-bridge.service >/dev/null 2>&1 || true
  rm -f "${SYSTEMD_DIR}/et312-rfcomm.service" "${SYSTEMD_DIR}/et312-mqtt-bridge.service"
  systemctl daemon-reload
}

print_summary() {
  local device_ids
  device_ids="$("${INSTALL_DIR}/.venv/bin/python" \
    "${INSTALL_DIR}/scripts/et312_rpi_manager.py" \
    --install-dir "${INSTALL_DIR}" \
    list-device-ids || true)"

  cat <<EOF

ET312 Raspberry Pi bridge installed.

Shared config:
  ${BRIDGE_CONFIG_FILE}
  ${DISCOVERY_CONFIG_FILE}

Device configs:
  ${INSTALL_DIR}/config/devices/

Configured device ids:
${device_ids:-  <none yet>}

Useful commands:
  sudo ./scripts/install_rpi_bluetooth_serial.sh --discover
  sudo ./scripts/update_rpi_bridge.sh
  sudo systemctl list-units 'et312-*'
  sudo journalctl -u 'et312-rfcomm-*' -u 'et312-mqtt-bridge-*' -f
EOF
}

main() {
  require_root
  parse_args "$@"
  install_system_packages
  ensure_service_user
  install_app_files
  initialize_config_layout
  register_initial_serial_device
  fix_config_permissions
  generated_units="$(generate_units)"
  disable_legacy_units
  enable_and_start_units "${generated_units}"
  print_summary
}

main "$@"
