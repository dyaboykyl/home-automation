# Deploying to a Raspberry Pi 3

This walks you through getting the thermostat running on a Raspberry Pi 3 from a
fresh SD card to a running, auto-starting service.

## 1. Prepare the Pi

Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager. In the
Imager's settings (gear icon) set the hostname, enable **SSH**, and configure
Wi-Fi — then you can run the Pi headless.

> Note: the Pi 3 shares one antenna between Wi-Fi and Bluetooth. If BLE scans
> are flaky, prefer a wired Ethernet connection, or keep the Meter/Bot within a
> few metres of the Pi.

Boot the Pi and SSH in:

```bash
ssh pi@raspberrypi.local
```

Update the OS and make sure the Bluetooth stack is present:

```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y git python3-venv python3-pip bluez
sudo systemctl enable --now bluetooth
bluetoothctl --version    # confirm BlueZ >= 5.43
```

## 2. Get the code onto the Pi

Pick whichever is convenient:

**Option A — clone from git (recommended):**
```bash
cd ~
git clone <your-repo-url> home-automation
cd home-automation
```

**Option B — copy from your computer with rsync/scp:**
```bash
# Run this on your laptop, from the project directory:
rsync -av --exclude .venv --exclude .git ./ pi@raspberrypi.local:~/home-automation/
# then SSH into the Pi and: cd ~/home-automation
```

## 3. Install

```bash
./scripts/install.sh
```

This creates a virtualenv in `.venv`, installs the package and its
dependencies (`bleak`, `PyYAML`), copies `config.example.yaml` to `config.yaml`,
and installs a systemd unit with the paths/user rewritten to match this
checkout. It does **not** start the service yet.

## 4. Discover your devices

```bash
.venv/bin/switchbot-thermostat scan
```

You'll see something like:

```
  E1:22:33:44:55:66  rssi= -58   21.4C  meter      Meter
  C2:33:44:55:66:77  rssi= -61    -     bot/other  Bot
```

The device showing a temperature is your **Meter**; the other is the **Bot**.
Copy each address into `config.yaml`.

> Can't tell them apart? Open the SwitchBot phone app → device → Settings →
> *Device Info* shows the BLE MAC for each.

## 5. Configure

```bash
nano config.yaml
```

At minimum set:

```yaml
meter:
  mac: "E1:22:33:44:55:66"
bot:
  mac: "C2:33:44:55:66:77"
  mode: toggle
control:
  target_temperature: 21.0
  hysteresis: 0.5
```

Tip: start with `safety: { dry_run: true }` so the loop logs what it *would* do
without actually pressing the Bot. Watch a few cycles, confirm the decisions
look right, then set `dry_run: false`.

## 6. Test before going live

```bash
# Sensor:
.venv/bin/switchbot-thermostat -c config.yaml read
# Decision the loop would make right now:
.venv/bin/switchbot-thermostat -c config.yaml status
# Button (make sure the Bot physically presses the thermostat):
.venv/bin/switchbot-thermostat -c config.yaml press
```

## 7. Run it as a service

```bash
sudo systemctl enable --now switchbot-thermostat.service
systemctl status switchbot-thermostat.service
journalctl -u switchbot-thermostat.service -f      # live logs
```

`enable --now` starts it immediately **and** makes it start on every boot.
`Restart=on-failure` in the unit means it recovers automatically from transient
BLE errors.

## 8. Updating later

```bash
cd ~/home-automation
git pull                       # or rsync again
.venv/bin/pip install .        # reinstall the package
sudo systemctl restart switchbot-thermostat.service
```

If you edit `config.yaml`, just restart the service to pick up changes:

```bash
sudo systemctl restart switchbot-thermostat.service
```

## Staying reachable

A power surge once left this Pi booting perfectly but invisible on the network.
The unclean shutdown truncated its saved Wi-Fi profile to zero bytes — the file
was still there, just empty — so NetworkManager had nothing to connect with,
`wlan0` sat at `NO-CARRIER`, and nothing answered `thermostat.local`. It looked
exactly like a dead Pi, and recovering it needed physical access.

`scripts/harden-network.sh` (run automatically by `install.sh`, and re-runnable
any time with `thermostat harden`) installs five independent layers so that no
single failure can hide the Pi again:

| Layer | What it does |
|---|---|
| `netconfig-guard` | Snapshots every network config file to `/var/backups/netconfig` after each successful connection. At boot, *before* NetworkManager starts, restores any file that is missing or zero-length. |
| `net-watchdog` | Every 5 min, checks it can reach the default gateway. On failure it escalates: restore config → restart NetworkManager → unblock radios and re-activate everything → reboot (rate-limited to once an hour, never within 10 min of boot). |
| Hardware watchdog | Already enabled by Raspberry Pi OS. A wedged kernel resets the board instead of hanging powered-on-but-dead. |
| Static fallback IPs | `192.168.1.250` on `eth0`, `192.168.1.251` on `wlan0`, *alongside* DHCP. These work with no DHCP server and no mDNS. |
| Infinite autoconnect | NetworkManager defaults to giving up after 4 failed attempts. Set to retry forever — on a headless box, giving up is a one-way trip offline. |

`scripts/thermostat` matches this on the client side: it tries the SSH alias,
then mDNS, then the static addresses, so a broken `.local` cannot lock you out.

Useful commands:

```bash
thermostat where        # which address answered, and how
thermostat netstatus    # guard status, units, addresses, watchdog
thermostat harden       # re-install the layers (idempotent)
thermostat wifi <ssid>  # set Wi-Fi credentials; prompts, never echoes,
                        # and the secret never enters argv or shell history
```

The guard is deliberately conservative. It only ever restores a file that is
**missing or zero-length**, so editing a connection by hand is always safe; and
it refuses to snapshot an empty file, so a corruption event cannot overwrite the
good backup that fixes it. It only forgets a profile you deleted when the network
is healthy at the time — meaning the deletion was deliberate.

Two things it cannot do in software, worth knowing:

- **It does not prevent SD card corruption.** It makes the network config
  recoverable, but other files remain at risk. A UPS, or even a cheap surge
  protector, is the real fix for the underlying cause.
- **Wired Ethernet remains the most reliable option.** With the cable in, Wi-Fi
  becomes a redundant second path rather than the only one.

## Troubleshooting

| symptom | fix |
|---|---|
| `scan` finds nothing | `sudo systemctl status bluetooth`; ensure `bluez` installed; move devices closer |
| "No advertisement from meter…within 30s" | wrong `meter.mac`, Meter out of range, or radio busy — increase `meter.scan_timeout`, try wired Ethernet |
| Bot connect errors / timeouts | Bot out of range or busy; increase `bot.connect_retries`/`connect_timeout`; make sure the SwitchBot app isn't connected to it at the same time |
| Permission/`org.bluez` errors | the service runs with `CAP_NET_ADMIN`/`CAP_NET_RAW`; if running by hand, your user must be able to use Bluetooth (it normally can on Raspberry Pi OS) |
| Heat toggles too often | raise `control.hysteresis` and/or `control.min_cycle_time` |
| State seems "stuck" after manual changes | delete `state.json` and restart to reset the believed on/off state |
| Pi drops off the network while idle (`.local` and IP both unreachable, but PWR LED solid) | Wi-Fi power-save on the Pi 3's shared radio. `install.sh` disables it via the `wifi-powersave-off.service`; check `iw dev wlan0 get power_save` (should say "off"). For a 24/7 setup, wired **Ethernet** is the most reliable fix. |
| Unreachable after a power cut; `thermostat.local` does not resolve | Try `thermostat where` — it falls back to the static addresses. On the Pi, `sudo netconfig-guard status` shows whether a config file was lost; `journalctl -b -u netconfig-guard-restore` shows what was restored at boot. |
| `wlan0` shows `NO-CARRIER` and `nmcli connection show` lists no Wi-Fi profile | The saved profile was lost. `sudo netconfig-guard restore` recovers it from the last snapshot; if there is no snapshot, re-add it with `thermostat wifi <ssid>`. |
| Pi 3 Model B cannot see your network | Its BCM43430 radio is **2.4 GHz only**. If your router advertises separate names per band, it needs the 2.4 GHz SSID. Check what it can actually see with `nmcli device wifi list`. |
