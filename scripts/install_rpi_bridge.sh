#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="et312-mqtt-bridge"
SERVICE_USER="et312"
INSTALL_DIR="/opt/et312-mqtt-bridge"
CONFIG_FILE="/etc/default/${SERVICE_NAME}"
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
    --exclude 'References/' \
    --exclude '__pycache__/' \
    --exclude '.DS_Store' \
    "${REPO_ROOT}/" "${INSTALL_DIR}/"

  python3 -m venv "${INSTALL_DIR}/.venv"
  "${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
  "${INSTALL_DIR}/.venv/bin/pip" install pyserial paho-mqtt

  chmod 0755 "${INSTALL_DIR}/scripts/install_rpi_bridge.sh"
  chmod 0755 "${INSTALL_DIR}/scripts/run_et312_mqtt_bridge.sh"
  chown -R root:root "${INSTALL_DIR}"
}

write_config() {
  install -m 0750 -d "$(dirname "${CONFIG_FILE}")"

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
EOF

  chown root:"${SERVICE_USER}" "${CONFIG_FILE}"
  chmod 0640 "${CONFIG_FILE}"
}

write_systemd_unit() {
  cat > "${SYSTEMD_UNIT}" <<EOF
[Unit]
Description=ET312 MQTT Bridge
After=network-online.target
Wants=network-online.target

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
