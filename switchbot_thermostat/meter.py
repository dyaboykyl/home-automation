"""High-level temperature source backed by a SwitchBot Meter."""

from __future__ import annotations

import logging

from . import ble
from .config import MeterConfig
from .models import Reading

_LOGGER = logging.getLogger(__name__)


class Meter:
    """Reads calibrated temperature from a SwitchBot Meter over BLE."""

    def __init__(self, config: MeterConfig):
        self._config = config

    async def read(self) -> Reading | None:
        """Return one reading with the configured calibration offset applied.

        Returns ``None`` if no advertisement was decoded within the timeout.
        """
        raw = await ble.read_meter(self._config.mac, self._config.scan_timeout)
        if raw is None:
            return None
        calibrated = Reading(
            temperature_c=raw.temperature_c + self._config.temperature_offset,
            humidity=raw.humidity,
            battery=raw.battery,
            rssi=raw.rssi,
        )
        _LOGGER.debug(
            "Meter %s: %.1fC (raw %.1fC), humidity=%s, battery=%s",
            self._config.mac,
            calibrated.temperature_c,
            raw.temperature_c,
            raw.humidity,
            raw.battery,
        )
        return calibrated
