#!/usr/bin/env bash
#
# harden-network.sh — make the Pi's network connection survivable.
#
# Run on the Pi. Idempotent: safe to re-run after every deploy.
#
# After a power surge truncated this Pi's Wi-Fi profile to zero bytes, it sat
# there booting perfectly with no way to reach it. This installs the layers that
# stop that from recurring:
#
#   1. netconfig-guard   — network config is snapshotted and restored at boot
#   2. net-watchdog      — connectivity loss self-heals, escalating to a reboot
#   3. hardware watchdog — a wedged kernel resets the board instead of hanging
#   4. static fallback IP— a known address that works with no DHCP and no mDNS
#   5. infinite retries  — NetworkManager never gives up on a connection
set -uo pipefail
fail() { echo "    !! $*" >&2; FAILED=1; }
FAILED=0

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ETH_STATIC="${ETH_STATIC:-192.168.1.250}"   # secondary address on eth0
WLAN_STATIC="${WLAN_STATIC:-192.168.1.251}" # secondary address on wlan0
PREFIX="${STATIC_PREFIX:-24}"

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo $0" >&2; exit 1; }

echo "==> Installing guard + watchdog scripts to /usr/local/sbin..."
install -m 0755 "$REPO_DIR/scripts/netconfig-guard" /usr/local/sbin/netconfig-guard
install -m 0755 "$REPO_DIR/scripts/net-watchdog"    /usr/local/sbin/net-watchdog

echo "==> Installing systemd units..."
for u in netconfig-guard-restore.service netconfig-guard-backup.service \
         netconfig-guard-backup.timer net-watchdog.service net-watchdog.timer; do
  install -m 0644 "$REPO_DIR/systemd/$u" "/etc/systemd/system/$u"
done

echo "==> Installing NetworkManager dispatcher hook..."
install -d -m 0755 /etc/NetworkManager/dispatcher.d
install -m 0755 "$REPO_DIR/systemd/90-netconfig-backup" \
                /etc/NetworkManager/dispatcher.d/90-netconfig-backup

echo "==> Checking the hardware watchdog..."
# If the kernel wedges, the BCM2835 watchdog resets the board rather than leaving
# it powered-on-but-unreachable, which looks identical to "off the network".
# Raspberry Pi OS already enables this via
# /usr/lib/systemd/system.conf.d/40-rpi-enable-watchdog.conf, so only add our own
# drop-in if it is genuinely off. Note drop-ins are merged in filename order, so
# ours must sort *after* 40-* to take effect.
rm -f /etc/systemd/system.conf.d/10-watchdog.conf   # superseded; never won anyway
current_wd=$(systemctl show -p RuntimeWatchdogUSec --value 2>/dev/null)
if [ -z "$current_wd" ] || [ "$current_wd" = "0" ] || [ "$current_wd" = "off" ]; then
  install -d -m 0755 /etc/systemd/system.conf.d
  cat > /etc/systemd/system.conf.d/99-watchdog.conf <<'EOF'
[Manager]
RuntimeWatchdogSec=1min
RebootWatchdogSec=2min
EOF
  echo "    enabled hardware watchdog (takes effect after reboot)"
else
  echo "    already enabled by the OS (RuntimeWatchdogSec=$current_wd) — leaving alone"
fi

echo "==> Making NetworkManager persistent about reconnecting..."
# The default is to give up after 4 failed autoconnect attempts. On a device
# with no keyboard that is a one-way trip offline; 0 means retry forever.
for uuid in $(nmcli -t -f UUID,TYPE connection show 2>/dev/null | awk -F: '$2=="802-3-ethernet"||$2=="802-11-wireless"{print $1}'); do
  name=$(nmcli -t -f connection.id connection show uuid "$uuid" 2>/dev/null | cut -d: -f2-)
  nmcli connection modify uuid "$uuid" \
      connection.autoconnect yes \
      connection.autoconnect-retries 0 \
      ipv4.dhcp-hostname thermostat \
      ipv4.may-fail yes 2>/dev/null && echo "    persistent autoconnect: $name"
done

echo "==> Adding static fallback addresses (survive DHCP and mDNS failure)..."
add_static() {           # $1=device  $2=address
  local dev="$1" addr="$2" uuid name
  # NOTE: the field is CON-UUID here; `device status` has no plain UUID field.
  uuid=$(nmcli -t -f DEVICE,CON-UUID device status 2>/dev/null | awk -F: -v d="$dev" '$1==d{print $2}' | head -1)
  [ -n "$uuid" ] || uuid=$(nmcli -t -f UUID,DEVICE connection show 2>/dev/null | awk -F: -v d="$dev" '$2==d{print $1}' | head -1)
  if [ -z "$uuid" ]; then echo "    (no connection bound to $dev yet — skipping $addr)"; return 0; fi
  name=$(nmcli -t -f connection.id connection show uuid "$uuid" 2>/dev/null | cut -d: -f2-)
  if nmcli -t -f ipv4.addresses connection show uuid "$uuid" 2>/dev/null | grep -q "$addr"; then
    echo "    $dev already has $addr"; return 0
  fi
  # method=auto keeps DHCP primary; the extra address is applied alongside it.
  if nmcli connection modify uuid "$uuid" ipv4.method auto +ipv4.addresses "${addr}/${PREFIX}"; then
    echo "    $dev ($name): DHCP + fallback $addr"
    # `reapply` activates the new address in place, without tearing the link
    # down -- important when this is running over SSH on that same interface.
    nmcli device reapply "$dev" >/dev/null 2>&1 || echo "      (applies on next reconnect)"
  else
    fail "could not add $addr to $dev"
  fi
}
add_static eth0  "$ETH_STATIC"
add_static wlan0 "$WLAN_STATIC"

echo "==> Reloading systemd and enabling units..."
systemctl daemon-reload
systemctl enable netconfig-guard-restore.service >/dev/null
systemctl enable --now netconfig-guard-backup.timer >/dev/null
systemctl enable --now net-watchdog.timer >/dev/null

echo "==> Taking the first configuration snapshot..."
/usr/local/sbin/netconfig-guard backup

echo
if [ "$FAILED" -ne 0 ]; then
  echo "WARNING: some steps failed (see !! above) — re-run after fixing." >&2
fi
echo "Done. Current protection:"
/usr/local/sbin/netconfig-guard status
