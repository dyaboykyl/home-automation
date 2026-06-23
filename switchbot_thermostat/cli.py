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

from . import __version__, ble
from .bot import Bot
from .config import ConfigError, load_config
from .controller import Controller, decide_heating
from .logging_setup import configure_logging
from .meter import Meter
from .schedule import resolve_target
from .state import State
from datetime import datetime

_LOGGER = logging.getLogger(__name__)
DEFAULT_CONFIG = "config.yaml"


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
    sub.add_parser("press", help="Manually press the configured Bot.")
    sub.add_parser("on", help="Manually switch the Bot ON.")
    sub.add_parser("off", help="Manually switch the Bot OFF.")
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
    return 0


async def _cmd_status(cfg) -> int:
    reading = await Meter(cfg.meter).read()
    state = State.load(cfg.state_file)
    if reading is None:
        print("No reading available.")
        return 1
    unit = cfg.control.unit
    symbol = "F" if unit == "fahrenheit" else "C"
    temperature = reading.temperature(unit)
    target = resolve_target(datetime.now(), cfg.schedule, cfg.control)
    decision = decide_heating(temperature, target, state.heating, cfg.control, cfg.safety)
    print(f"Temperature:  {temperature:.1f}{symbol}")
    print(f"Target:       {target:.1f}{symbol}  (±{cfg.control.hysteresis}{symbol} deadband)")
    print(f"Believed:     {'HEATING' if state.heating else 'OFF'}")
    print(f"Desired:      {'HEATING' if decision.desired_heating else 'OFF'}  ({decision.reason})")
    if decision.desired_heating != state.heating:
        print("-> Next cycle would actuate the Bot (subject to min_cycle_time).")
    else:
        print("-> No change needed.")
    return 0


async def _cmd_bot(cfg, command: str) -> int:
    bot = Bot(cfg.bot)
    if cfg.safety.dry_run:
        print(f"[dry-run] Would send '{command}' to bot {cfg.bot.mac}.")
        return 0
    await {"press": bot.press, "on": bot.turn_on, "off": bot.turn_off}[command]()
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
    try:
        if args.command == "run":
            return asyncio.run(_cmd_run(cfg))
        if args.command == "read":
            return asyncio.run(_cmd_read(cfg))
        if args.command == "status":
            return asyncio.run(_cmd_status(cfg))
        if args.command in ("press", "on", "off"):
            return asyncio.run(_cmd_bot(cfg, args.command))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
