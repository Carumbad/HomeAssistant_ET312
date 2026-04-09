#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="et312-mqtt-bridge"
SERVICE_USER="et312"
INSTALL_DIR="/opt/et312-mqtt-bridge"
CONFIG_FILE="${INSTALL_DIR}/config/${SERVICE_NAME}.env"
SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DEVICE=""
MQTT_HOST="127.0.0.1"
MQTT_PORT="1883"
MQTT_USERNAME=""
MQTT_PASSWORD=""
STATE_TOPIC="et312/state"
COMMAND_TOPIC="et312/command"
AVAILABILITY_TOPIC="et312/availability"
POLL_INTERVAL="2.0"
TIMEOUT="1.0"
BAUDRATE="19200"
STARTUP_DELAY="1.5"
SYNC_ATTEMPTS="40"
SYNC_READ_TIMEOUT="0.35"
SYNC_INTER_ATTEMPT_DELAY="0.1"
POST_SYNC_DELAY="0.2"
KEY_EXCHANGE_TIMEOUT="1.5"
CONNECT_RETRIES="1"
RECONNECT_DELAY="2.0"

usage() {
  cat <<EOF
Usage:
  sudo ./scripts/install_rpi_bridge.sh --device /dev/ttyUSB0 [options]

Options:
  --device PATH              Serial device for the ET312. Required.
  --mqtt-host HOST           MQTT broker host. Default: ${MQTT_HOST}
  --mqtt-port PORT           MQTT broker port. Default: ${MQTT_PORT}
  --mqtt-username USER       MQTT username.
  --mqtt-password PASS       MQTT password.
  --state-topic TOPIC        MQTT state topic. Default: ${STATE_TOPIC}
  --command-topic TOPIC      MQTT command topic. Default: ${COMMAND_TOPIC}
  --availability-topic TOPIC MQTT availability topic. Default: ${AVAILABILITY_TOPIC}
  --poll-interval SECONDS    State publish interval. Default: ${POLL_INTERVAL}
  --timeout SECONDS          Serial timeout. Default: ${TIMEOUT}
  --baudrate BAUD            Serial baudrate. Default: ${BAUDRATE}
  --startup-delay SECONDS    Delay after opening the serial device. Default: ${STARTUP_DELAY}
  --sync-attempts COUNT      Sync byte attempts per connect. Default: ${SYNC_ATTEMPTS}
  --sync-read-timeout SEC    Per-sync read timeout. Default: ${SYNC_READ_TIMEOUT}
  --sync-gap SEC             Delay between sync attempts. Default: ${SYNC_INTER_ATTEMPT_DELAY}
  --post-sync-delay SEC      Delay after sync before key exchange. Default: ${POST_SYNC_DELAY}
  --key-timeout SEC          Key exchange timeout. Default: ${KEY_EXCHANGE_TIMEOUT}
  --connect-retries COUNT    Serial reconnect attempts. Default: ${CONNECT_RETRIES}
  --reconnect-delay SEC      Delay between reconnect attempts. Default: ${RECONNECT_DELAY}
  --install-dir PATH         App install location. Default: ${INSTALL_DIR}
  --service-user USER        Service account. Default: ${SERVICE_USER}
  --help                     Show this help.

Example:
  sudo ./scripts/install_rpi_bridge.sh \\
    --device /dev/ttyUSB0 \\
    --mqtt-host 192.168.1.20 \\
    --mqtt-username et312 \\
    --mqtt-password supersecret
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
      --state-topic)
        STATE_TOPIC="$2"
        shift 2
        ;;
      --command-topic)
        COMMAND_TOPIC="$2"
        shift 2
        ;;
      --availability-topic)
        AVAILABILITY_TOPIC="$2"
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

  if [[ -z "${DEVICE}" ]]; then
    echo "--device is required." >&2
    usage >&2
    exit 1
  fi
}

install_system_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y python3 python3-venv python3-pip rsync
}

ensure_service_user() {
  if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/var/lib/${SERVICE_NAME}" --shell /usr/sbin/nologin "${SERVICE_USER}"
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

  chmod 0755 "${INSTALL_DIR}/scripts/install_rpi_bridge.sh"
  chmod 0755 "${INSTALL_DIR}/scripts/run_et312_mqtt_bridge.sh"
  if [[ -f "${INSTALL_DIR}/scripts/run_et312_rfcomm.sh" ]]; then
    chmod 0755 "${INSTALL_DIR}/scripts/run_et312_rfcomm.sh"
  fi
  if [[ -f "${INSTALL_DIR}/scripts/release_et312_rfcomm.sh" ]]; then
    chmod 0755 "${INSTALL_DIR}/scripts/release_et312_rfcomm.sh"
  fi
  chown -R root:root "${INSTALL_DIR}"

  if [[ -d "${INSTALL_DIR}/config" ]]; then
    chown root:"${SERVICE_USER}" "${INSTALL_DIR}/config"
    chmod 0750 "${INSTALL_DIR}/config"
    find "${INSTALL_DIR}/config" -maxdepth 1 -type f -exec chown root:"${SERVICE_USER}" {} +
    find "${INSTALL_DIR}/config" -maxdepth 1 -type f -exec chmod 0640 {} +
  fi
}

write_config() {
  install -m 0750 -o root -g "${SERVICE_USER}" -d "$(dirname "${CONFIG_FILE}")"

  cat > "${CONFIG_FILE}" <<EOF
DEVICE="${DEVICE}"
BAUDRATE="${BAUDRATE}"
TIMEOUT="${TIMEOUT}"
MQTT_HOST="${MQTT_HOST}"
MQTT_PORT="${MQTT_PORT}"
MQTT_USERNAME="${MQTT_USERNAME}"
MQTT_PASSWORD="${MQTT_PASSWORD}"
STATE_TOPIC="${STATE_TOPIC}"
COMMAND_TOPIC="${COMMAND_TOPIC}"
AVAILABILITY_TOPIC="${AVAILABILITY_TOPIC}"
POLL_INTERVAL="${POLL_INTERVAL}"
STARTUP_DELAY="${STARTUP_DELAY}"
SYNC_ATTEMPTS="${SYNC_ATTEMPTS}"
SYNC_READ_TIMEOUT="${SYNC_READ_TIMEOUT}"
SYNC_INTER_ATTEMPT_DELAY="${SYNC_INTER_ATTEMPT_DELAY}"
POST_SYNC_DELAY="${POST_SYNC_DELAY}"
KEY_EXCHANGE_TIMEOUT="${KEY_EXCHANGE_TIMEOUT}"
CONNECT_RETRIES="${CONNECT_RETRIES}"
RECONNECT_DELAY="${RECONNECT_DELAY}"
EOF

  chown root:"${SERVICE_USER}" "${CONFIG_FILE}"
  chmod 0640 "${CONFIG_FILE}"
}

write_systemd_unit() {
  local unit_after="After=network-online.target"
  local unit_wants="Wants=network-online.target"

  if [[ "${DEVICE}" == /dev/rfcomm* ]]; then
    unit_after="After=network-online.target et312-rfcomm.service"
    unit_wants="Wants=network-online.target et312-rfcomm.service"
  fi

  cat > "${SYSTEMD_UNIT}" <<EOF
[Unit]
Description=ET312 MQTT Bridge
${unit_after}
${unit_wants}

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/scripts/run_et312_mqtt_bridge.sh ${CONFIG_FILE}
Restart=on-failure
RestartSec=5

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

ET312 MQTT bridge installed.

Useful commands:
  sudo systemctl status ${SERVICE_NAME}
  sudo journalctl -u ${SERVICE_NAME} -f
  sudo editor ${CONFIG_FILE}
  sudo systemctl restart ${SERVICE_NAME}

If the ET312 appears under a different serial path later, update:
  ${CONFIG_FILE}
EOF
}

main() {
  require_root
  parse_args "$@"
  install_system_packages
  ensure_service_user
  install_app_files
  write_config
  write_systemd_unit
  enable_service
  print_summary
}

main "$@"
