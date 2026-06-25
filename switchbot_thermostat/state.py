"""Persisted controller state so restarts don't double-toggle the thermostat."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass

_LOGGER = logging.getLogger(__name__)


@dataclass
class State:
    """What the controller believes about the wall thermostat.

    ``heating`` is the believed on/off state we last drove the Bot to.
    ``last_action_ts`` is the wall-clock epoch of the last actuation, used to
    enforce the anti-short-cycle minimum cycle time across restarts.
    ``off_timer_at`` is the wall-clock epoch at which a pending auto-off timer
    should turn the thermostat off (0 = no timer); it survives restarts.
    """

    heating: bool = False
    last_action_ts: float = 0.0
    off_timer_at: float = 0.0

    @classmethod
    def load(cls, path: str) -> "State":
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return cls(
                heating=bool(data.get("heating", False)),
                last_action_ts=float(data.get("last_action_ts", 0.0)),
                off_timer_at=float(data.get("off_timer_at", 0.0)),
            )
        except (OSError, ValueError, TypeError) as exc:
            _LOGGER.warning("Could not read state file %s (%s); starting fresh.", path, exc)
            return cls()

    def save(self, path: str) -> None:
        """Atomically write the state to ``path``."""
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(asdict(self), fh)
            os.replace(tmp, path)
        except OSError as exc:
            _LOGGER.error("Could not persist state to %s: %s", path, exc)
            if os.path.exists(tmp):
                os.unlink(tmp)
