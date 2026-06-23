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
| `read`    | take a single temperature reading                             |
| `status`  | show current temp, target, and the decision the loop would make |
| `press` / `on` / `off` | manually drive the Bot to test wiring            |
| `run`     | start the control loop (what the systemd service runs)        |

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest        # 24 tests: meter decoding, hysteresis, schedule, config
```

The BLE advertisement byte layouts and the control logic are covered by unit
tests so they can be changed safely without hardware.
