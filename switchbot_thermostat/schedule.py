"""Resolve the active target temperature from an optional weekly schedule."""

from __future__ import annotations

from datetime import datetime

from .config import ControlConfig, ScheduleConfig

_WEEK_MINUTES = 7 * 24 * 60


def resolve_target(now: datetime, schedule: ScheduleConfig, control: ControlConfig) -> float:
    """Return the target temperature in effect at ``now``.

    Picks the schedule period whose start (day + time) was passed most
    recently, wrapping across the week boundary. Falls back to
    ``control.target_temperature`` when the schedule is disabled or empty.
    """
    if not schedule.enabled or not schedule.periods:
        return control.target_temperature

    now_minute = now.weekday() * 24 * 60 + now.hour * 60 + now.minute
    best_delta: int | None = None
    best_target = control.target_temperature
    for period in schedule.periods:
        for day in period.days:
            start = day * 24 * 60 + period.start_minute
            delta = (now_minute - start) % _WEEK_MINUTES  # minutes since this start fired
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_target = period.target
    return best_target
