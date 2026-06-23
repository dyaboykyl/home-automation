"""The thermostat control loop: decide desired state, then actuate the Bot."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime

from .bot import Bot
from .config import Config, ControlConfig, SafetyConfig
from .meter import Meter
from .models import Reading
from .schedule import resolve_target
from .state import State

_LOGGER = logging.getLogger(__name__)


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


class Controller:
    """Owns the meter, bot, persisted state, and the evaluation loop."""

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

    async def tick(self) -> Decision | None:
        """Run one evaluation: read, decide, and actuate if needed."""
        reading = await self._meter.read()
        if reading is None:
            _LOGGER.warning("No meter reading this cycle; leaving thermostat unchanged.")
            return None

        unit = self._config.control.unit
        temperature = reading.temperature(unit)
        target = resolve_target(self._now_fn(), self._config.schedule, self._config.control)

        decision = decide_heating(
            temperature,
            target,
            self._state.heating,
            self._config.control,
            self._config.safety,
        )

        symbol = "°F" if unit == "fahrenheit" else "°C"
        _LOGGER.info(
            "temp=%.1f%s target=%.1f%s desired=%s (%s) current=%s",
            temperature, symbol, target, symbol,
            "HEAT" if decision.desired_heating else "OFF",
            decision.reason,
            "HEAT" if self._state.heating else "OFF",
        )
        await self._actuate(decision)
        return decision

    async def _actuate(self, decision: Decision) -> None:
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

        if self._config.safety.dry_run:
            _LOGGER.warning(
                "[dry-run] Would set thermostat to %s.",
                "HEAT" if decision.desired_heating else "OFF",
            )
        else:
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
