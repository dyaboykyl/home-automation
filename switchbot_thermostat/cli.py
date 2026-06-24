"""Command-line interface.

Subcommands:
    run     Start the thermostat control loop (the daemon / systemd entry).
    scan    Discover nearby SwitchBot devices and their MAC addresses.
    read    Take a single temperature reading from the configured meter.
    status  Show current temperature, target, and what the controller would do.
    press   Manually press the Bot (also: `on` / `off`) to test wiring.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import replace

from . import __version__, ble, runtime
from .bot import Bot
from .config import ConfigError, load_config
from .controller import Controller, decide_heating, effective_settings
from .logging_setup import configure_logging
from .meter import Meter
from .runtime import Overrides
from .state import State
from datetime import datetime

_LOGGER = logging.getLogger(__name__)
DEFAULT_CONFIG = "config.yaml"


def _verb(action: str) -> str:
    """The conditioning word for an action: 'cooling' for cool, else 'heating'."""
    return "cooling" if action == "cool" else "heating"


def _state_label(on: bool, action: str) -> str:
    """Human label for the believed actuator state, e.g. 'ON (cooling)' / 'OFF'."""
    return f"ON ({_verb(action)})" if on else "OFF"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="switchbot-thermostat", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-c", "--config", default=DEFAULT_CONFIG,
        help=f"Path to the YAML config file (default: {DEFAULT_CONFIG}).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Start the control loop.")
    sub.add_parser("read", help="Take one temperature reading.")
    sub.add_parser("status", help="Show current temperature, target, and decision.")
    scan = sub.add_parser("scan", help="Discover nearby SwitchBot devices.")
    scan.add_argument("--timeout", type=float, default=10.0, help="Scan duration in seconds.")
    for _name, _help in (
        ("press", "Manually press the configured Bot."),
        ("on", "Manually switch the Bot ON."),
        ("off", "Manually switch the Bot OFF."),
    ):
        _p = sub.add_parser(_name, help=_help)
        _p.add_argument(
            "-f", "--force", action="store_true",
            help="Send the command even when dry-run is on.",
        )
        _p.add_argument(
            "-t", "--timeout", type=float, default=12.0,
            help="Per-attempt BLE timeout in seconds (default 12; manual commands try once).",
        )

    sub.add_parser("config", help="Show effective settings (config + live overrides).")
    settable = ", ".join(runtime.SETTABLE)
    sset = sub.add_parser("set", help=f"Set a live override ({settable}).")
    sset.add_argument("key", choices=list(runtime.SETTABLE))
    sset.add_argument("value")
    sget = sub.add_parser("get", help="Get the current value of a setting.")
    sget.add_argument("key", choices=list(runtime.SETTABLE))
    sunset = sub.add_parser("unset", help="Clear a live override (revert to config.yaml).")
    sunset.add_argument("key", choices=list(runtime.SETTABLE))
    sub.add_parser("pause", help="Pause actuation (keep reading/logging, never press).")
    sub.add_parser("resume", help="Resume actuation after a pause.")
    sstate = sub.add_parser(
        "state", help="Show or correct the believed thermostat on/off state."
    )
    sstate.add_argument(
        "value", nargs="?", choices=["on", "off"],
        help="Set the believed state (on=heating, off); omit to just show it.",
    )
    return parser


async def _cmd_scan(args) -> int:
    print(f"Scanning for SwitchBot devices for {args.timeout:.0f}s...\n")
    devices = await ble.discover(args.timeout)
    if not devices:
        print("No SwitchBot devices found. Make sure Bluetooth is on and devices are nearby.")
        return 1
    for d in devices:
        temp = f"{d['temperature_c']:.1f}C" if d["temperature_c"] is not None else "  -  "
        print(f"  {d['address']}  rssi={d['rssi']:>4}  {temp:>7}  {d['looks_like']:<10} {d['name'] or ''}")
    print("\nCopy the meter address into meter.mac and the bot address into bot.mac.")
    return 0


async def _cmd_read(cfg) -> int:
    reading = await Meter(cfg.meter).read()
    if reading is None:
        print("No reading: the meter did not advertise within the timeout.")
        return 1
    unit = cfg.control.unit
    symbol = "F" if unit == "fahrenheit" else "C"
    print(f"Temperature: {reading.temperature(unit):.1f}{symbol}")
    if reading.humidity is not None:
        print(f"Humidity:    {reading.humidity}%")
    if reading.battery is not None:
        print(f"Battery:     {reading.battery}%")
    if reading.rssi is not None:
        print(f"RSSI:        {reading.rssi} dBm")
    overrides = Overrides.load(cfg.overrides_file)
    action = overrides.action or cfg.control.action
    state = State.load(cfg.state_file)
    print(f"Thermostat:  {_state_label(state.heating, action)}  (believed state)")
    return 0


async def _cmd_status(cfg) -> int:
    state = State.load(cfg.state_file)
    overrides = Overrides.load(cfg.overrides_file)
    eff = effective_settings(cfg, overrides, datetime.now())
    reading = await Meter(cfg.meter).read()
    if reading is None:
        print("No reading available.")
        return 1
    unit = eff.control.unit
    symbol = "F" if unit == "fahrenheit" else "C"
    temperature = reading.temperature(unit)
    decision = decide_heating(temperature, eff.target, state.heating, eff.control, cfg.safety)
    print(f"Temperature:  {temperature:.1f}{symbol}")
    print(f"Target:       {eff.target:.1f}{symbol}  (±{eff.control.hysteresis}{symbol} deadband, from {eff.target_source})")
    print(f"Mode:         {eff.control.action}  (bot: {cfg.bot.mode})")
    print(f"Believed:     {_state_label(state.heating, eff.control.action)}")
    print(f"Desired:      {_state_label(decision.desired_heating, eff.control.action)}  ({decision.reason})")
    if eff.dry_run:
        print("Dry-run:      ON (will not press the Bot)")
    if eff.paused:
        print("Paused:       YES (actuation suspended)")
    if eff.paused:
        print("-> Paused: no action will be taken.")
    elif decision.desired_heating != state.heating:
        print("-> Next cycle would actuate the Bot (subject to min_cycle_time).")
    else:
        print("-> No change needed.")
    return 0


def _cmd_config(cfg) -> int:
    overrides = Overrides.load(cfg.overrides_file)
    eff = effective_settings(cfg, overrides, datetime.now())
    unit = eff.control.unit
    symbol = "F" if unit == "fahrenheit" else "C"
    print("Devices:")
    print(f"  meter.mac          {cfg.meter.mac}")
    print(f"  bot.mac            {cfg.bot.mac}  (mode: {cfg.bot.mode})")
    print("Settings (effective value <- source):")
    print(f"  target             {eff.target:.1f}{symbol}   <- {eff.target_source}")
    print(f"  hysteresis         {eff.control.hysteresis}{symbol}")
    print(f"  action             {eff.control.action}")
    print(f"  dry-run            {'on' if eff.dry_run else 'off'}")
    print(f"  paused             {'yes' if eff.paused else 'no'}")
    print(f"  poll_interval      {cfg.control.poll_interval:.0f}s")
    print(f"  min_cycle_time     {cfg.control.min_cycle_time:.0f}s")
    active = {k: runtime.get_value(overrides, k) for k in runtime.SETTABLE
              if runtime.get_value(overrides, k) is not None}
    if active or overrides.paused:
        print("Live overrides active (use `unset <key>` to clear):")
        for k, v in active.items():
            print(f"  {k} = {v}")
        if overrides.paused:
            print("  paused = true")
    return 0


def _cmd_set(cfg, key: str, value: str) -> int:
    overrides = Overrides.load(cfg.overrides_file)
    try:
        runtime.set_value(overrides, key, value)
    except ValueError as exc:
        print(f"Invalid value for '{key}': {exc}", file=sys.stderr)
        return 2
    overrides.save(cfg.overrides_file)
    print(f"Set {key} = {runtime.get_value(overrides, key)} (takes effect next cycle).")
    return 0


def _cmd_get(cfg, key: str) -> int:
    overrides = Overrides.load(cfg.overrides_file)
    value = runtime.get_value(overrides, key)
    print(f"{key} = {value if value is not None else '(unset; using config.yaml)'}")
    return 0


def _cmd_unset(cfg, key: str) -> int:
    overrides = Overrides.load(cfg.overrides_file)
    runtime.clear_value(overrides, key)
    overrides.save(cfg.overrides_file)
    print(f"Cleared override '{key}' (reverts to config.yaml next cycle).")
    return 0


def _cmd_pause(cfg, paused: bool) -> int:
    overrides = Overrides.load(cfg.overrides_file)
    overrides.paused = paused
    overrides.save(cfg.overrides_file)
    print("Actuation paused." if paused else "Actuation resumed.")
    return 0


def _cmd_state(cfg, value: str | None) -> int:
    overrides = Overrides.load(cfg.overrides_file)
    action = overrides.action or cfg.control.action
    state = State.load(cfg.state_file)
    if value is None:
        print(f"Believed thermostat state: {_state_label(state.heating, action)}")
        return 0
    state.heating = value == "on"
    # A manual correction is not a Bot press, so clear the short-cycle timer:
    # the controller should be free to actuate on the next cycle if needed.
    state.last_action_ts = 0.0
    state.save(cfg.state_file)
    print(
        f"Set believed thermostat state to {_state_label(state.heating, action)}. "
        "The controller will act on this at the next cycle."
    )
    return 0


async def _cmd_bot(cfg, command: str, force: bool = False, timeout: float = 12.0) -> int:
    # Manual commands fail fast: a single attempt with a short timeout, so the
    # user isn't left waiting through the control loop's retry/backoff sequence.
    bot = Bot(replace(cfg.bot, connect_retries=1, connect_timeout=timeout))
    overrides = Overrides.load(cfg.overrides_file)
    dry_run = effective_settings(cfg, overrides, datetime.now()).dry_run
    if dry_run and not force:
        print(
            f"[dry-run] Would send '{command}' to bot {cfg.bot.mac}. "
            "Use --force to send it for real."
        )
        return 0
    if dry_run:
        print(f"[dry-run override] Forcing real '{command}'...")
    try:
        await {"press": bot.press, "on": bot.turn_on, "off": bot.turn_off}[command]()
    except Exception as exc:
        print(f"Failed to send '{command}' to bot {cfg.bot.mac}: {exc}", file=sys.stderr)
        return 1
    print(f"Sent '{command}' to bot {cfg.bot.mac}.")
    return 0


async def _cmd_run(cfg) -> int:
    meter = Meter(cfg.meter)
    bot = Bot(cfg.bot)
    state = State.load(cfg.state_file)
    controller = Controller(cfg, meter, bot, state)
    await controller.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # `scan` does not need a config file.
    if args.command == "scan":
        return asyncio.run(_cmd_scan(args))

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        return 2

    configure_logging(cfg.logging)

    # Synchronous (no BLE) commands.
    if args.command == "config":
        return _cmd_config(cfg)
    if args.command == "set":
        return _cmd_set(cfg, args.key, args.value)
    if args.command == "get":
        return _cmd_get(cfg, args.key)
    if args.command == "unset":
        return _cmd_unset(cfg, args.key)
    if args.command in ("pause", "resume"):
        return _cmd_pause(cfg, args.command == "pause")
    if args.command == "state":
        return _cmd_state(cfg, args.value)

    try:
        if args.command == "run":
            return asyncio.run(_cmd_run(cfg))
        if args.command == "read":
            return asyncio.run(_cmd_read(cfg))
        if args.command == "status":
            return asyncio.run(_cmd_status(cfg))
        if args.command in ("press", "on", "off"):
            return asyncio.run(_cmd_bot(cfg, args.command, force=args.force, timeout=args.timeout))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
