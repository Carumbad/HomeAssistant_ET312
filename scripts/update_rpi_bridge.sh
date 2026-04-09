#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="et312-mqtt-bridge"
RFCOMM_SERVICE_NAME="et312-rfcomm"
SERVICE_USER="et312"
INSTALL_DIR="/opt/et312-mqtt-bridge"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ALLOW_DIRTY="0"
SKIP_PULL="0"

usage() {
  cat <<EOF
Usage:
  sudo ./scripts/update_rpi_bridge.sh [options]

Options:
  --skip-pull      Do not run git pull before installing the update.
  --allow-dirty    Allow updating even if the local repo has uncommitted changes.
  --help           Show this help.

This script is intended to run on the Raspberry Pi bridge host. It:
  - pulls the latest checked-out branch with git
  - stops the ET312 bridge and RFCOMM services cleanly
  - refreshes /opt/et312-mqtt-bridge from the current repo checkout
  - preserves existing config files in /opt/et312-mqtt-bridge/config/
  - restarts RFCOMM first (if installed), then the MQTT bridge
EOF
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Please run this updater with sudo or as root." >&2
    exit 1
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --skip-pull)
        SKIP_PULL="1"
        shift
        ;;
      --allow-dirty)
        ALLOW_DIRTY="1"
        shift
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

run_in_repo() {
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    sudo -u "${SUDO_USER}" "$@"
  else
    "$@"
  fi
}

ensure_clean_repo() {
  if [[ "${ALLOW_DIRTY}" == "1" ]]; then
    return
  fi

  if ! run_in_repo git -C "${REPO_ROOT}" diff --quiet --ignore-submodules -- || \
     ! run_in_repo git -C "${REPO_ROOT}" diff --cached --quiet --ignore-submodules --; then
    echo "Refusing to update from a repo with uncommitted changes." >&2
    echo "Commit/stash them first, or rerun with --allow-dirty." >&2
    exit 1
  fi
}

pull_latest() {
  if [[ "${SKIP_PULL}" == "1" ]]; then
    return
  fi

  ensure_clean_repo
  run_in_repo git -C "${REPO_ROOT}" pull --ff-only
}

stop_services() {
  if systemctl list-unit-files "${SERVICE_NAME}.service" >/dev/null 2>&1; then
    systemctl stop "${SERVICE_NAME}" || true
  fi

  if systemctl list-unit-files "${RFCOMM_SERVICE_NAME}.service" >/dev/null 2>&1; then
    systemctl stop "${RFCOMM_SERVICE_NAME}" || true
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

sync_app_files() {
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
  chown -R root:root "${INSTALL_DIR}"

  if [[ -d "${INSTALL_DIR}/config" ]]; then
    chown root:"${SERVICE_USER}" "${INSTALL_DIR}/config"
    chmod 0750 "${INSTALL_DIR}/config"
    find "${INSTALL_DIR}/config" -maxdepth 1 -type f -exec chown root:"${SERVICE_USER}" {} +
    find "${INSTALL_DIR}/config" -maxdepth 1 -type f -exec chmod 0640 {} +
  fi
}

start_services() {
  systemctl daemon-reload

  if systemctl list-unit-files "${RFCOMM_SERVICE_NAME}.service" >/dev/null 2>&1; then
    systemctl start "${RFCOMM_SERVICE_NAME}"
  fi

  if systemctl list-unit-files "${SERVICE_NAME}.service" >/dev/null 2>&1; then
    systemctl start "${SERVICE_NAME}"
  fi
}

print_summary() {
  echo
  echo "ET312 bridge update complete."
  echo
  echo "Useful commands:"
  echo "  sudo systemctl status ${RFCOMM_SERVICE_NAME} ${SERVICE_NAME}"
  echo "  sudo journalctl -u ${RFCOMM_SERVICE_NAME} -u ${SERVICE_NAME} -f"
}

main() {
  require_root
  parse_args "$@"
  pull_latest
  stop_services
  install_system_packages
  ensure_service_user
  sync_app_files
  start_services
  print_summary
}

main "$@"
