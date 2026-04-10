#!/usr/bin/env bash

set -euo pipefail

SERVICE_USER="et312"
INSTALL_DIR="/opt/et312-mqtt-bridge"
SYSTEMD_DIR="/etc/systemd/system"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ALLOW_DIRTY="0"
SKIP_PULL="0"
RUN_DISCOVERY="0"

usage() {
  cat <<EOF
Usage:
  sudo ./scripts/update_rpi_bridge.sh [options]

Options:
  --skip-pull      Do not run git pull before installing the update.
  --allow-dirty    Allow updating even if the local repo has uncommitted changes.
  --discover       Run Bluetooth discovery before regenerating units.
  --help           Show this help.

This script is intended to run on the Raspberry Pi bridge host. It:
  - pulls the latest checked-out branch with git
  - stops all configured ET312 bridge and RFCOMM services cleanly
  - refreshes /opt/et312-mqtt-bridge from the current repo checkout
  - preserves existing config files in /opt/et312-mqtt-bridge/config/
  - optionally runs Bluetooth discovery
  - regenerates per-device systemd units from the known-device registry
  - restarts RFCOMM units first, then all bridge units
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
      --discover)
        RUN_DISCOVERY="1"
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
  local units=()
  local legacy_units=(
    "et312-rfcomm.service"
    "et312-mqtt-bridge.service"
  )
  while IFS= read -r unit_name; do
    [[ -z "${unit_name}" ]] && continue
    units+=("${unit_name}")
  done < <(systemctl list-units --all --plain --no-legend 'et312-rfcomm-*.service' 'et312-mqtt-bridge-*.service' | awk '{print $1}')

  units+=("${legacy_units[@]}")

  systemctl stop "${units[@]}" >/dev/null 2>&1 || true
}

install_system_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y python3 python3-venv python3-pip rsync bluez bluez-tools rfkill
}

ensure_service_user() {
  if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/var/lib/et312" --shell /usr/sbin/nologin "${SERVICE_USER}"
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
  chmod 0755 "${INSTALL_DIR}/scripts/et312_rpi_manager.py"
  chown -R root:root "${INSTALL_DIR}"

  if [[ -d "${INSTALL_DIR}/config" ]]; then
    chown root:"${SERVICE_USER}" "${INSTALL_DIR}/config"
    chmod 0750 "${INSTALL_DIR}/config"
    if [[ -d "${INSTALL_DIR}/config/devices" ]]; then
      chown root:"${SERVICE_USER}" "${INSTALL_DIR}/config/devices"
      chmod 0750 "${INSTALL_DIR}/config/devices"
    fi
    find "${INSTALL_DIR}/config" -type f -exec chown root:"${SERVICE_USER}" {} +
    find "${INSTALL_DIR}/config" -type f -exec chmod 0640 {} +
  fi
}

run_discovery() {
  if [[ "${RUN_DISCOVERY}" != "1" ]]; then
    return
  fi

  rfkill unblock bluetooth || true
  systemctl enable --now bluetooth

  "${INSTALL_DIR}/.venv/bin/python" \
    "${INSTALL_DIR}/scripts/et312_rpi_manager.py" \
    --install-dir "${INSTALL_DIR}" \
    discover-bluetooth >/dev/null
}

generate_units() {
  "${INSTALL_DIR}/.venv/bin/python" \
    "${INSTALL_DIR}/scripts/et312_rpi_manager.py" \
    --install-dir "${INSTALL_DIR}" \
    --systemd-dir "${SYSTEMD_DIR}" \
    ensure-layout

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
  local legacy_units=(
    "et312-rfcomm.service"
    "et312-mqtt-bridge.service"
  )

  systemctl disable --now "${legacy_units[@]}" >/dev/null 2>&1 || true
  rm -f "${SYSTEMD_DIR}/et312-rfcomm.service" "${SYSTEMD_DIR}/et312-mqtt-bridge.service"
  systemctl daemon-reload
}

print_summary() {
  echo
  echo "ET312 multi-device bridge update complete."
  echo
  echo "Useful commands:"
  echo "  sudo systemctl list-units 'et312-*'"
  echo "  sudo journalctl -u 'et312-rfcomm-*' -u 'et312-mqtt-bridge-*' -f"
  echo "  sudo ${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/scripts/et312_rpi_manager.py --install-dir ${INSTALL_DIR} list-device-ids"
}

main() {
  require_root
  parse_args "$@"
  pull_latest
  stop_services
  install_system_packages
  ensure_service_user
  sync_app_files
  "${INSTALL_DIR}/.venv/bin/python" \
    "${INSTALL_DIR}/scripts/et312_rpi_manager.py" \
    --install-dir "${INSTALL_DIR}" \
    migrate-legacy-config >/dev/null || true
  run_discovery
  generated_units="$(generate_units)"
  disable_legacy_units
  enable_and_start_units "${generated_units}"
  print_summary
}

main "$@"
