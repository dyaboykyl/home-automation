"""Tests for the actuation path: dry-run, real press, and anti-short-cycle."""

import asyncio

from switchbot_thermostat.config import Config
from switchbot_thermostat.controller import Controller, Decision
from switchbot_thermostat.state import State


class FakeBot:
    def __init__(self):
        self.calls = []

    async def apply(self, on):
        self.calls.append(on)


def _ctrl(tmp_path, heating=False, last_ts=0.0):
    cfg = Config.from_dict({
        "meter": {"mac": "AA:BB:CC:DD:EE:FF"},
        "bot": {"mac": "11:22:33:44:55:66"},
        "state_file": str(tmp_path / "s.json"),
    })
    bot = FakeBot()
    controller = Controller(cfg, None, bot, State(heating=heating, last_action_ts=last_ts), clock=lambda: 1000.0)
    return controller, bot, cfg


def test_dry_run_does_not_press_or_change_state(tmp_path):
    controller, bot, cfg = _ctrl(tmp_path, heating=False)
    asyncio.run(controller._actuate(Decision(27.9, 26.0, True, "t"), dry_run=True))
    assert bot.calls == []  # no press
    assert State.load(cfg.state_file).heating is False  # believed state untouched


def test_real_actuation_presses_and_updates_state(tmp_path):
    controller, bot, cfg = _ctrl(tmp_path, heating=False)
    asyncio.run(controller._actuate(Decision(27.9, 26.0, True, "t"), dry_run=False))
    assert bot.calls == [True]
    assert State.load(cfg.state_file).heating is True


def test_no_action_when_already_in_desired_state(tmp_path):
    controller, bot, cfg = _ctrl(tmp_path, heating=True)
    asyncio.run(controller._actuate(Decision(27.9, 26.0, True, "t"), dry_run=False))
    assert bot.calls == []  # already on


def test_min_cycle_suppresses_rapid_change(tmp_path):
    # clock=1000, last action at 999 -> elapsed 1s < 300s min_cycle.
    controller, bot, cfg = _ctrl(tmp_path, heating=False, last_ts=999.0)
    asyncio.run(controller._actuate(Decision(27.9, 26.0, True, "t"), dry_run=False))
    assert bot.calls == []
