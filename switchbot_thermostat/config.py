"""Configuration loading and validation.

The config is a YAML file mapped onto typed dataclasses so that the rest of the
program works with validated, autocomplete-friendly objects instead of raw
dicts. Validation errors raise ``ConfigError`` with an actionable message.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml

MAC_LEN = 17  # "AA:BB:CC:DD:EE:FF"
DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class ConfigError(ValueError):
    """Raised when the configuration file is missing or invalid."""


def _require(d: dict, key: str, where: str) -> Any:
    if key not in d:
        raise ConfigError(f"Missing required key '{key}' in '{where}'.")
    return d[key]


def _validate_mac(value: Any, where: str) -> str:
    if not isinstance(value, str) or len(value) != MAC_LEN or value.count(":") != 5:
        raise ConfigError(
            f"'{where}' must be a BLE MAC like 'AA:BB:CC:DD:EE:FF', got {value!r}. "
            "Run `switchbot-thermostat scan` to discover device addresses."
        )
    return value.upper()


def _one_of(value: Any, options: set[str], where: str) -> str:
    if value not in options:
        raise ConfigError(
            f"'{where}' must be one of {sorted(options)}, got {value!r}."
        )
    return value


@dataclass
class MeterConfig:
    mac: str
    scan_timeout: float = 30.0
    temperature_offset: float = 0.0  # degrees C added to raw reading (calibration)
    max_reading_age: float = 300.0  # reject readings older than this many seconds

    @classmethod
    def from_dict(cls, d: dict) -> "MeterConfig":
        return cls(
            mac=_validate_mac(_require(d, "mac", "meter"), "meter.mac"),
            scan_timeout=float(d.get("scan_timeout", 30.0)),
            temperature_offset=float(d.get("temperature_offset", 0.0)),
            max_reading_age=float(d.get("max_reading_age", 300.0)),
        )


@dataclass
class BotConfig:
    mac: str
    mode: str = "toggle"  # toggle | switch | momentary
    password: str | None = None
    connect_retries: int = 3
    connect_timeout: float = 20.0
    invert: bool = False  # if True, a "heat on" maps to bot OFF/release

    @classmethod
    def from_dict(cls, d: dict) -> "BotConfig":
        return cls(
            mac=_validate_mac(_require(d, "mac", "bot"), "bot.mac"),
            mode=_one_of(d.get("mode", "toggle"), {"toggle", "switch", "momentary"}, "bot.mode"),
            password=d.get("password"),
            connect_retries=int(d.get("connect_retries", 3)),
            connect_timeout=float(d.get("connect_timeout", 20.0)),
            invert=bool(d.get("invert", False)),
        )


@dataclass
class SchedulePeriod:
    days: list[int]
    start_minute: int  # minutes since midnight
    target: float

    @classmethod
    def from_dict(cls, d: dict, idx: int) -> "SchedulePeriod":
        where = f"schedule.periods[{idx}]"
        raw_days = _require(d, "days", where)
        days: list[int] = []
        for name in raw_days:
            key = str(name).strip().lower()[:3]
            if key not in DAYS:
                raise ConfigError(f"{where}.days has invalid day {name!r}; use mon..sun.")
            days.append(DAYS[key])
        start = str(_require(d, "start", where))
        try:
            hh, mm = start.split(":")
            start_minute = int(hh) * 60 + int(mm)
        except (ValueError, AttributeError):
            raise ConfigError(f"{where}.start must be 'HH:MM', got {start!r}.")
        if not 0 <= start_minute < 24 * 60:
            raise ConfigError(f"{where}.start must be between 00:00 and 23:59.")
        return cls(days=days, start_minute=start_minute, target=float(_require(d, "target", where)))


@dataclass
class ScheduleConfig:
    enabled: bool = False
    periods: list[SchedulePeriod] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict | None) -> "ScheduleConfig":
        d = d or {}
        periods = [SchedulePeriod.from_dict(p, i) for i, p in enumerate(d.get("periods", []))]
        return cls(enabled=bool(d.get("enabled", False)), periods=periods)


@dataclass
class ControlConfig:
    unit: str = "celsius"  # celsius | fahrenheit (applies to all thresholds below)
    target_temperature: float = 21.0
    hysteresis: float = 0.5
    poll_interval: float = 60.0
    min_cycle_time: float = 300.0
    action: str = "heat"  # heat | cool

    @classmethod
    def from_dict(cls, d: dict | None) -> "ControlConfig":
        d = d or {}
        hysteresis = float(d.get("hysteresis", 0.5))
        if hysteresis <= 0:
            raise ConfigError("control.hysteresis must be greater than 0.")
        return cls(
            unit=_one_of(d.get("unit", "celsius"), {"celsius", "fahrenheit"}, "control.unit"),
            target_temperature=float(d.get("target_temperature", 21.0)),
            hysteresis=hysteresis,
            poll_interval=float(d.get("poll_interval", 60.0)),
            min_cycle_time=float(d.get("min_cycle_time", 300.0)),
            action=_one_of(d.get("action", "heat"), {"heat", "cool"}, "control.action"),
        )


@dataclass
class SafetyConfig:
    min_temperature: float | None = 5.0  # frost protection (in control.unit)
    max_temperature: float | None = 30.0  # overheat cutoff
    dry_run: bool = False

    @classmethod
    def from_dict(cls, d: dict | None) -> "SafetyConfig":
        d = d or {}

        # Distinguish an omitted key (keep the default) from an explicit
        # ``null`` (disable the safety limit).
        def _temp(key: str, default: float | None) -> float | None:
            if key not in d:
                return default
            return None if d[key] is None else float(d[key])

        return cls(
            min_temperature=_temp("min_temperature", 5.0),
            max_temperature=_temp("max_temperature", 30.0),
            dry_run=bool(d.get("dry_run", False)),
        )


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str | None = None

    @classmethod
    def from_dict(cls, d: dict | None) -> "LoggingConfig":
        d = d or {}
        return cls(level=str(d.get("level", "INFO")).upper(), file=d.get("file"))


@dataclass
class Config:
    meter: MeterConfig
    bot: BotConfig
    control: ControlConfig
    schedule: ScheduleConfig
    safety: SafetyConfig
    logging: LoggingConfig
    state_file: str = "state.json"

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        if not isinstance(d, dict):
            raise ConfigError("Config root must be a mapping.")
        return cls(
            meter=MeterConfig.from_dict(_require(d, "meter", "<root>")),
            bot=BotConfig.from_dict(_require(d, "bot", "<root>")),
            control=ControlConfig.from_dict(d.get("control")),
            schedule=ScheduleConfig.from_dict(d.get("schedule")),
            safety=SafetyConfig.from_dict(d.get("safety")),
            logging=LoggingConfig.from_dict(d.get("logging")),
            state_file=str(d.get("state_file", "state.json")),
        )


def load_config(path: str) -> Config:
    """Load and validate a YAML config file into a :class:`Config`."""
    if not os.path.exists(path):
        raise ConfigError(
            f"Config file not found: {path}\n"
            "Copy config.example.yaml to config.yaml and edit it."
        )
    with open(path, "r", encoding="utf-8") as fh:
        try:
            raw = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ConfigError(f"Could not parse YAML in {path}: {exc}") from exc
    return Config.from_dict(raw or {})
