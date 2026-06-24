"""The thermostat control loop: decide desired state, then actuate the Bot."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime

from .bot import Bot
from .config import Config, ControlConfig, SafetyConfig
from .meter import Meter
from .models import Reading
from .runtime import Overrides
from .schedule import resolve_target
from .state import State

_LOGGER = logging.getLogger(__name__)


@dataclass
class Effective:
    """Config values after live overrides are applied for one evaluation."""

    control: ControlConfig
    target: float
    dry_run: bool
    paused: bool
    target_source: str  # override | schedule | config


def effective_settings(config: Config, overrides: Overrides, now: datetime) -> Effective:
    """Merge static config with live overrides into the values used this cycle."""
    control = replace(
        config.control,
        hysteresis=overrides.hysteresis if overrides.hysteresis is not None else config.control.hysteresis,
        action=overrides.action if overrides.action is not None else config.control.action,
    )
    if overrides.target_temperature is not None:
        target, source = overrides.target_temperature, "override"
    elif config.schedule.enabled and config.schedule.periods:
        target, source = resolve_target(now, config.schedule, control), "schedule"
    else:
        target, source = control.target_temperature, "config"
    dry_run = overrides.dry_run if overrides.dry_run is not None else config.safety.dry_run
    return Effective(control, target, dry_run, overrides.paused, source)


@dataclass
class Decision:
    """The outcome of one control evaluation (returned for logging/testing)."""

    temperature: float  # in control.unit
    target: float
    desired_heating: bool
    reason: str


def decide_heating(
    temperature: float,
    target: float,
    previous: bool,
    control: ControlConfig,
    safety: SafetyConfig,
) -> Decision:
    """Pure hysteresis + safety decision.

    All values are in ``control.unit``. ``previous`` is the last desired state,
    held within the deadband. Safety thresholds override the hysteresis band.
    """
    h = control.hysteresis
    heat = control.action == "heat"

    # Safety overrides first — they win regardless of the deadband.
    if safety.min_temperature is not None and temperature < safety.min_temperature:
        desired = heat  # frost protection: heat ON / cool OFF
        return Decision(temperature, target, desired, "safety:min_temperature")
    if safety.max_temperature is not None and temperature > safety.max_temperature:
        desired = not heat  # overheat cutoff: heat OFF / cool ON
        return Decision(temperature, target, desired, "safety:max_temperature")

    if heat:
        if temperature <= target - h:
            return Decision(temperature, target, True, "below band -> heat on")
        if temperature >= target + h:
            return Decision(temperature, target, False, "above band -> heat off")
    else:  # cooling
        if temperature >= target + h:
            return Decision(temperature, target, True, "above band -> cool on")
        if temperature <= target - h:
            return Decision(temperature, target, False, "below band -> cool off")

    return Decision(temperature, target, previous, "within deadband -> hold")


def build_status(
    config: Config,
    overrides: Overrides,
    state: State,
    reading: Reading | None,
    reading_age: float | None,
    now: datetime,
) -> dict:
    """Build a JSON-serialisable status snapshot (used by the web API and tests)."""
    eff = effective_settings(config, overrides, now)
    unit = eff.control.unit
    temperature = reading.temperature(unit) if reading is not None else None
    decision = (
        decide_heating(temperature, eff.target, state.heating, eff.control, config.safety)
        if temperature is not None
        else None
    )
    return {
        "temperature": round(temperature, 1) if temperature is not None else None,
        "unit": unit,
        "humidity": reading.humidity if reading else None,
        "battery": reading.battery if reading else None,
        "rssi": reading.rssi if reading else None,
        "reading_age": round(reading_age, 1) if reading_age is not None else None,
        "target": round(eff.target, 1),
        "target_source": eff.target_source,
        "hysteresis": eff.control.hysteresis,
        "action": eff.control.action,
        "believed": state.heating,
        "desired": decision.desired_heating if decision else None,
        "reason": decision.reason if decision else None,
        "dry_run": eff.dry_run,
        "paused": eff.paused,
        "bot_mode": config.bot.mode,
        "min_cycle_time": config.control.min_cycle_time,
    }


class Controller:
    """Owns the meter, bot, persisted state, and the evaluation loop.

    All Bluetooth access (meter reads and Bot commands, from both the control
    loop and the web API) is serialised through ``_ble_lock`` so the single
    radio is never used by two coroutines at once. The latest reading is cached
    so the web UI can show status instantly without forcing a new scan.
    """

    def __init__(
        self,
        config: Config,
        meter: Meter,
        bot: Bot,
        state: State,
        *,
        now_fn=datetime.now,
        clock=time.time,
    ):
        self._config = config
        self._meter = meter
        self._bot = bot
        self._state = state
        self._now_fn = now_fn  # wall datetime, for schedule resolution
        self._clock = clock  # epoch seconds, for min-cycle timing
        self._ble_lock = asyncio.Lock()
        self._last_reading: Reading | None = None
        self._last_reading_ts: float = 0.0

    # -- status / web-facing API ------------------------------------------- #
    def get_status(self) -> dict:
        """Current status snapshot from the cached reading + live files."""
        overrides = Overrides.load(self._config.overrides_file)
        state = State.load(self._config.state_file)
        age = (self._clock() - self._last_reading_ts) if self._last_reading else None
        return build_status(
            self._config, overrides, state, self._last_reading, age, self._now_fn()
        )

    async def refresh(self) -> dict:
        """Force a live meter read (serialised on the BLE lock), return status."""
        async with self._ble_lock:
            reading = await self._meter.read()
        if reading is not None:
            self._last_reading = reading
            self._last_reading_ts = self._clock()
        return self.get_status()

    async def apply_output(self, on: bool, *, force: bool = False) -> dict:
        """Manually drive the actuator to on/off and record the believed state."""
        state = State.load(self._config.state_file)
        overrides = Overrides.load(self._config.overrides_file)
        dry_run = (
            overrides.dry_run if overrides.dry_run is not None else self._config.safety.dry_run
        )
        if on == state.heating:
            return {"changed": False, "reason": "already in that state", "heating": on}
        if dry_run and not force:
            return {"changed": False, "dry_run": True, "heating": state.heating}
        async with self._ble_lock:
            await self._bot.apply(on)
        state.heating = on
        state.last_action_ts = self._clock()
        state.save(self._config.state_file)
        self._state = state
        return {"changed": True, "heating": on}

    def correct_state(self, on: bool) -> None:
        """Set the believed state without touching hardware (clears cycle timer)."""
        state = State.load(self._config.state_file)
        state.heating = on
        state.last_action_ts = 0.0
        state.save(self._config.state_file)
        self._state = state

    # -- control loop ------------------------------------------------------ #
    async def tick(self) -> Decision | None:
        """Run one evaluation: read, decide, and actuate if needed."""
        self._state = State.load(self._config.state_file)  # pick up external corrections
        overrides = Overrides.load(self._config.overrides_file)
        eff = effective_settings(self._config, overrides, self._now_fn())

        async with self._ble_lock:
            reading = await self._meter.read()
        if reading is None:
            _LOGGER.warning("No meter reading this cycle; leaving thermostat unchanged.")
            return None
        self._last_reading = reading
        self._last_reading_ts = self._clock()

        unit = eff.control.unit
        temperature = reading.temperature(unit)
        decision = decide_heating(
            temperature, eff.target, self._state.heating, eff.control, self._config.safety
        )

        symbol = "°F" if unit == "fahrenheit" else "°C"
        _LOGGER.info(
            "temp=%.1f%s target=%.1f%s (%s) desired=%s (%s) current=%s%s",
            temperature, symbol, eff.target, symbol, eff.target_source,
            "ON" if decision.desired_heating else "OFF",
            decision.reason,
            "ON" if self._state.heating else "OFF",
            " [PAUSED]" if eff.paused else "",
        )

        if eff.paused:
            return decision  # evaluate and log, but never actuate while paused
        await self._actuate(decision, dry_run=eff.dry_run)
        return decision

    async def _actuate(self, decision: Decision, *, dry_run: bool) -> None:
        if decision.desired_heating == self._state.heating:
            return  # already in the desired state

        now = self._clock()
        elapsed = now - self._state.last_action_ts
        min_cycle = self._config.control.min_cycle_time
        if self._state.last_action_ts and elapsed < min_cycle:
            _LOGGER.info(
                "Suppressing change for anti-short-cycle: %.0fs since last action < %.0fs.",
                elapsed, min_cycle,
            )
            return

        if dry_run:
            # Dry-run must change nothing — not the Bot, not the believed state.
            _LOGGER.warning(
                "[dry-run] Would set thermostat to %s (believed state left unchanged).",
                "ON" if decision.desired_heating else "OFF",
            )
            return

        async with self._ble_lock:
            await self._bot.apply(decision.desired_heating)
        self._state.heating = decision.desired_heating
        self._state.last_action_ts = now
        self._state.save(self._config.state_file)

    async def run(self) -> None:
        """Run the control loop forever, polling at ``control.poll_interval``."""
        interval = self._config.control.poll_interval
        _LOGGER.info("Starting control loop (poll every %.0fs).", interval)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # never let one bad cycle kill the daemon
                _LOGGER.exception("Unexpected error during control cycle.")
            await asyncio.sleep(interval)
