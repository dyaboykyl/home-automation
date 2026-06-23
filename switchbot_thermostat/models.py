"""Plain data structures shared across the package."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    """A single temperature reading from a SwitchBot Meter."""

    temperature_c: float
    humidity: int | None = None
    battery: int | None = None
    rssi: int | None = None

    def temperature(self, unit: str) -> float:
        if unit == "fahrenheit":
            return self.temperature_c * 9 / 5 + 32
        return self.temperature_c
