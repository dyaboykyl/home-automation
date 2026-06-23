"""High-level actuator backed by a SwitchBot Bot (button pusher)."""

from __future__ import annotations

import logging

from . import ble
from .config import BotConfig

_LOGGER = logging.getLogger(__name__)


class Bot:
    """Sends press / on / off commands to a SwitchBot Bot over BLE."""

    def __init__(self, config: BotConfig):
        self._config = config

    async def _send(self, command: str) -> None:
        await ble.send_bot_command(
            self._config.mac,
            command,
            password=self._config.password,
            retries=self._config.connect_retries,
            timeout=self._config.connect_timeout,
        )

    async def press(self) -> None:
        """Momentary press-and-release (toggle a button)."""
        await self._send("press")

    async def turn_on(self) -> None:
        """Switch-mode ON (Bot holds the button pressed)."""
        await self._send("on")

    async def turn_off(self) -> None:
        """Switch-mode OFF (Bot releases the button)."""
        await self._send("off")

    async def apply(self, heating: bool) -> None:
        """Drive the actuator to reflect a desired ``heating`` state.

        ``toggle`` / ``momentary`` modes assume the caller only invokes this on
        a genuine state transition, and issue a single press. ``switch`` mode
        maps the desired state directly onto the Bot's on/off, honouring the
        ``invert`` option for either mode.
        """
        physical_on = heating != self._config.invert
        if self._config.mode == "switch":
            await (self.turn_on() if physical_on else self.turn_off())
        else:  # toggle / momentary -> a single press flips the wall thermostat
            await self.press()
