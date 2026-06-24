# SwitchBot Thermostat

A lightweight, configurable thermostat for a **Raspberry Pi 3** (or any Linux
box with Bluetooth). It reads temperature from a **SwitchBot Meter** over
Bluetooth LE and presses a **SwitchBot Bot** that's stuck to your wall
thermostat — turning a "dumb" thermostat into a hysteresis-controlled one.

No SwitchBot Hub and no cloud account required — everything happens locally over
the Pi's built-in Bluetooth.

## How it works

```
SwitchBot Meter  --(BLE advertisement: temperature)-->  Raspberry Pi  --(BLE press)-->  SwitchBot Bot --> wall thermostat
```

1. The Pi passively listens for the Meter's BLE broadcast and decodes the
   temperature (no pairing/connection needed).
2. A hysteresis controller decides whether the heat *should* be on or off:
   - Heat **on** when temperature falls below `target − hysteresis`.
   - Heat **off** when it rises above `target + hysteresis`.
   - Inside that deadband, it holds the previous state (no chatter).
3. When the desired state changes, it presses the Bot. In **toggle** mode (the
   default) a single press flips the wall thermostat, and the believed state is
   tracked in `state.json` so a restart won't double-toggle.
4. An **anti-short-cycle** timer (`min_cycle_time`) prevents rapid on/off
   presses, and **frost/overheat** safety limits override the band.

## Bot modes

| mode        | behaviour                                                        |
|-------------|------------------------------------------------------------------|
| `toggle`    | one press flips heat on/off; we press only on state transitions  |
| `switch`    | Bot held down = on, released = off (uses the Bot's switch mode)   |
| `momentary` | a press triggers a temporary action (e.g. a boost button)        |

## Quick start (on the Raspberry Pi)

```bash
git clone <your-repo-url> home-automation
cd home-automation
./scripts/install.sh          # venv + deps + systemd service

# 1. Find your devices' BLE addresses:
.venv/bin/switchbot-thermostat scan

# 2. Edit config.yaml — set meter.mac and bot.mac (and target_temperature):
nano config.yaml

# 3. Test the sensor and the button:
.venv/bin/switchbot-thermostat -c config.yaml read
.venv/bin/switchbot-thermostat -c config.yaml status
.venv/bin/switchbot-thermostat -c config.yaml press

# 4. Run it as a background service:
sudo systemctl enable --now switchbot-thermostat.service
journalctl -u switchbot-thermostat.service -f
```

See [DEPLOY.md](DEPLOY.md) for a detailed, step-by-step Raspberry Pi guide.

## Configuration

All behaviour lives in `config.yaml` (copy from `config.example.yaml`). The most
important knobs:

- `control.target_temperature`, `control.hysteresis`, `control.unit`
- `control.action` — `heat` (actuator on when too cold; for heating) or `cool` (on when too warm; for AC)
- `control.min_cycle_time` — anti-short-cycle protection
- `bot.mode` — `toggle` / `switch` / `momentary`, plus `bot.invert`
- `schedule` — optional weekly setback (e.g. cooler at night)
- `safety.min_temperature` / `max_temperature` — frost & overheat limits
- `safety.dry_run` — log decisions without ever pressing the Bot (great for tuning)

Every option is commented in `config.example.yaml`.

## CLI

| command   | purpose                                                       |
|-----------|---------------------------------------------------------------|
| `scan`    | discover nearby SwitchBot devices and their MAC addresses     |
| `read`    | temperature + believed state (uses the daemon's cached reading if running, else a live scan) |
| `status`  | current temp, target, and the decision the loop would make (daemon-cached if running) |
| `health`  | fast one-glance check: service state, boot time, last change, live reading |
| `state [on\|off]` | show the believed on/off state, or correct it if it's drifted |
| `config`  | show all effective settings (config + live overrides)         |
| `set <key> <value>` | change a live setting (`target`, `hysteresis`, `action`, `dry-run`) |
| `get <key>` / `unset <key>` | read / clear a live override               |
| `pause` / `resume` | suspend / resume actuation (keeps reading + logging) |
| `press` / `on` / `off` | manually drive the Bot (add `--force` to send even in dry-run; `--timeout` to adjust) |
| `run`     | start the control loop (what the systemd service runs)        |

Manual `press`/`on`/`off` respect dry-run by default. Pass `--force` to send a
real command even while dry-run is on — handy for testing the Bot before going
live. They try once and fail fast (default 12s, tune with `--timeout`), unlike
the control loop which retries.

### Live settings

`config.yaml` holds the fixed setup (device addresses, Bot mode, schedule).
Day-to-day knobs are stored separately in `overrides.json` and applied on top,
so the running service picks up a change on its **next poll — no restart**:

```bash
switchbot-thermostat set target 22       # raise the target to 22°
switchbot-thermostat set dry-run on       # log decisions without pressing
switchbot-thermostat pause                # stop actuating (e.g. while away)
switchbot-thermostat unset target         # revert to config.yaml's value
```

## Phone control (web app / PWA)

The `run` daemon also serves a mobile web UI (no app store, no sideloading). On
your phone's browser, go to `http://<pi-ip>:8080` — it shows the live
temperature, target (with +/- stepper), mode, pause/auto, and manual on/off,
and updates every few seconds. Use **Add to Home Screen** for an app-like icon.

- The web server runs **inside** the control-loop daemon, sharing one Bluetooth
  lock — so the UI and the automation never fight over the radio.
- Find the URL quickly with `thermostat url` (or `thermostat open` on a Mac).
- It works on your home Wi-Fi. For remote (away-from-home) access later, set
  `web.auth_token` in `config.yaml` and put it behind a VPN/tunnel (e.g.
  Tailscale) — the UI already sends the token, so no rewrite is needed.

Configure it under `web:` in `config.yaml` (`enabled`, `host`, `port`, `auth_token`).

## Remote control from your Mac

`scripts/thermostat` is a wrapper that runs any of the above on the Pi over SSH
(via the `thermostat` host alias). Symlink it onto your PATH and drive the Pi
from your laptop:

```bash
thermostat read                 # current temperature
thermostat status               # temp, target, and what it would do
thermostat set target 22        # change target temperature (live)
thermostat config               # show all effective settings
thermostat press                # manually press the Bot
thermostat pause / resume       # suspend / resume actuation
thermostat service status       # systemd service state
thermostat enable-service       # start now + on every boot
thermostat logs                 # follow the live service log
thermostat deploy               # push local code changes to the Pi + restart
thermostat ssh                  # open a shell on the Pi
```

Override the target host with `export THERMOSTAT_HOST=pi@192.168.1.50`.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest        # 24 tests: meter decoding, hysteresis, schedule, config
```

The BLE advertisement byte layouts and the control logic are covered by unit
tests so they can be changed safely without hardware.
