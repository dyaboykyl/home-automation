"""Live, CLI-settable overrides applied on top of the static config.

``config.yaml`` holds the fixed setup (device addresses, Bot mode, schedule).
Day-to-day knobs you want to change without editing that file or restarting the
service — the target temperature, the deadband, dry-run, or a pause — live here
in a small JSON file. The running control loop reloads it every poll, so a
``set`` takes effect on the next cycle.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass

_LOGGER = logging.getLogger(__name__)


def _parse_bool(value: str) -> bool:
    v = str(value).strip().lower()
    if v in ("on", "true", "yes", "1", "enable", "enabled"):
        return True
    if v in ("off", "false", "no", "0", "disable", "disabled"):
        return False
    raise ValueError(f"expected a boolean (on/off), got {value!r}")


def _parse_action(value: str) -> str:
    v = str(value).strip().lower()
    if v not in ("heat", "cool"):
        raise ValueError(f"expected 'heat' or 'cool', got {value!r}")
    return v


def _parse_positive_float(value: str) -> float:
    f = float(value)
    if f <= 0:
        raise ValueError("must be greater than 0")
    return f


@dataclass
class Overrides:
    """Optional live overrides. ``None`` means "fall back to config.yaml"."""

    target_temperature: float | None = None  # in control.unit
    hysteresis: float | None = None
    action: str | None = None  # heat | cool
    dry_run: bool | None = None
    paused: bool = False

    @classmethod
    def load(cls, path: str) -> "Overrides":
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return cls(
                target_temperature=data.get("target_temperature"),
                hysteresis=data.get("hysteresis"),
                action=data.get("action"),
                dry_run=data.get("dry_run"),
                paused=bool(data.get("paused", False)),
            )
        except (OSError, ValueError, TypeError) as exc:
            _LOGGER.warning("Could not read overrides %s (%s); ignoring.", path, exc)
            return cls()

    def save(self, path: str) -> None:
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".overrides-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)
        os.replace(tmp, path)


@dataclass(frozen=True)
class Settable:
    attr: str
    parser: callable
    help: str


# Keys accepted by `set <key> <value>` / `get <key>`, mapped to Overrides fields.
SETTABLE: dict[str, Settable] = {
    "target": Settable("target_temperature", float, "target temperature in control.unit"),
    "hysteresis": Settable("hysteresis", _parse_positive_float, "deadband half-width (>0)"),
    "action": Settable("action", _parse_action, "heat | cool"),
    "dry-run": Settable("dry_run", _parse_bool, "on | off (log without pressing the Bot)"),
}


def set_value(overrides: Overrides, key: str, raw: str) -> None:
    """Validate and apply ``key=raw`` onto ``overrides`` in place."""
    if key not in SETTABLE:
        raise ValueError(f"unknown setting '{key}'. Settable: {', '.join(SETTABLE)}")
    spec = SETTABLE[key]
    setattr(overrides, spec.attr, spec.parser(raw))


def get_value(overrides: Overrides, key: str):
    if key not in SETTABLE:
        raise ValueError(f"unknown setting '{key}'. Gettable: {', '.join(SETTABLE)}")
    return getattr(overrides, SETTABLE[key].attr)


def clear_value(overrides: Overrides, key: str) -> None:
    if key not in SETTABLE:
        raise ValueError(f"unknown setting '{key}'. Settable: {', '.join(SETTABLE)}")
    setattr(overrides, SETTABLE[key].attr, None)
