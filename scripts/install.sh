#!/usr/bin/env bash
# Install the SwitchBot Thermostat on a Raspberry Pi (Raspberry Pi OS / Debian).
# Creates a virtualenv, installs the package, and sets up the systemd service.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_DIR}/.venv"
SERVICE_NAME="switchbot-thermostat.service"

echo "==> Installing system packages (Bluetooth stack + Python venv)..."
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3-venv python3-pip bluez iw
fi

# On the Pi 3, Wi-Fi and Bluetooth share one radio, and Wi-Fi power-save makes
# the board drop off the network while idle. Disable it persistently so the
# device stays reachable 24/7.
if command -v iw >/dev/null 2>&1 || [ -x /usr/sbin/iw ]; then
  echo "==> Disabling Wi-Fi power management (persistent)..."
  sudo tee /etc/systemd/system/wifi-powersave-off.service >/dev/null <<'EOF'
[Unit]
Description=Disable WiFi power management (Pi 3 BLE/WiFi coexistence)
After=network.target sys-subsystem-net-devices-wlan0.device

[Service]
Type=oneshot
ExecStart=/usr/sbin/iw dev wlan0 set power_save off
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now wifi-powersave-off.service || true
fi

echo "==> Creating virtualenv at ${VENV_DIR}..."
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip
# Editable install: the venv runs directly from this source tree, so updating
# the code (e.g. `thermostat deploy`) just needs an rsync, no wheel rebuild.
"${VENV_DIR}/bin/pip" install -e "${REPO_DIR}"

if [ ! -f "${REPO_DIR}/config.yaml" ]; then
  echo "==> Creating config.yaml from example (edit it before starting!)..."
  cp "${REPO_DIR}/config.example.yaml" "${REPO_DIR}/config.yaml"
fi

echo "==> Installing systemd service..."
SERVICE_SRC="${REPO_DIR}/systemd/${SERVICE_NAME}"
TMP_SERVICE="$(mktemp)"
# Rewrite the template paths/user to match this checkout and the current user.
sed -e "s|/home/pi/home-automation|${REPO_DIR}|g" \
    -e "s|^User=pi|User=$(id -un)|" \
    "${SERVICE_SRC}" > "${TMP_SERVICE}"
sudo cp "${TMP_SERVICE}" "/etc/systemd/system/${SERVICE_NAME}"
rm -f "${TMP_SERVICE}"
sudo systemctl daemon-reload

# A headless Pi with no keyboard attached has to be able to recover its own
# network. Installs the config guard, the connectivity watchdog, the hardware
# watchdog and the static fallback addresses. Safe to re-run.
echo "==> Hardening the network against config loss and dropouts..."
sudo bash "${REPO_DIR}/scripts/harden-network.sh"

cat <<EOF

Done.

Next steps:
  1. Discover your devices:   ${VENV_DIR}/bin/switchbot-thermostat scan
  2. Edit ${REPO_DIR}/config.yaml  (set meter.mac and bot.mac)
  3. Test a reading:          ${VENV_DIR}/bin/switchbot-thermostat -c ${REPO_DIR}/config.yaml read
  4. Test the Bot:            ${VENV_DIR}/bin/switchbot-thermostat -c ${REPO_DIR}/config.yaml press
  5. Enable the service:      sudo systemctl enable --now ${SERVICE_NAME}
  6. Watch the logs:          journalctl -u ${SERVICE_NAME} -f
EOF
