"""Tests for weekly schedule resolution."""

from datetime import datetime

from switchbot_thermostat.config import ControlConfig, ScheduleConfig, SchedulePeriod
from switchbot_thermostat.schedule import resolve_target

CONTROL = ControlConfig(target_temperature=18.0)


def _schedule():
    return ScheduleConfig(
        enabled=True,
        periods=[
            SchedulePeriod(days=[0, 1, 2, 3, 4], start_minute=6 * 60 + 30, target=21.0),
            SchedulePeriod(days=[0, 1, 2, 3, 4], start_minute=22 * 60, target=17.0),
            SchedulePeriod(days=[5, 6], start_minute=8 * 60, target=20.0),
        ],
    )


def test_disabled_uses_default_target():
    sched = ScheduleConfig(enabled=False, periods=[])
    assert resolve_target(datetime(2026, 6, 18, 9, 0), sched, CONTROL) == 18.0


def test_weekday_morning_period():
    # Thursday 09:00 -> after the 06:30 morning start.
    assert resolve_target(datetime(2026, 6, 18, 9, 0), _schedule(), CONTROL) == 21.0


def test_weekday_night_period():
    # Thursday 23:00 -> after the 22:00 night start.
    assert resolve_target(datetime(2026, 6, 18, 23, 0), _schedule(), CONTROL) == 17.0


def test_early_morning_wraps_to_previous_night():
    # Thursday 05:00 -> the most recent start is Wednesday 22:00 (night, 17.0).
    assert resolve_target(datetime(2026, 6, 18, 5, 0), _schedule(), CONTROL) == 17.0


def test_weekend_period():
    # Saturday 10:00 -> weekend 08:00 start (20.0).
    assert resolve_target(datetime(2026, 6, 20, 10, 0), _schedule(), CONTROL) == 20.0
