"""Tests for manual on/off compressor protection (anti rapid-toggle)."""

import asyncio

from switchbot_thermostat.config import Config
from switchbot_thermostat.controller import Controller
from switchbot_thermostat.state import State


class FakeBot:
    def __init__(self):
        self.calls = []

    async def apply(self, on):
        self.calls.append(on)


def _ctrl(tmp_path, heating, last_ts, clock, min_cycle=300):
    cfg = Config.from_dict({
        "meter": {"mac": "AA:BB:CC:DD:EE:FF"},
        "bot": {"mac": "11:22:33:44:55:66"},
        "control": {"min_cycle_time": min_cycle},
        "state_file": str(tmp_path / "s.json"),
        "overrides_file": str(tmp_path / "o.json"),
    })
    State(heating=heating, last_action_ts=last_ts).save(cfg.state_file)
    bot = FakeBot()
    controller = Controller(cfg, None, bot, State(), clock=lambda: clock)
    return controller, bot, cfg


def test_manual_on_blocked_within_min_cycle(tmp_path):
    # last change at t=900, clock=1000 -> elapsed 100 < 300 -> ON blocked.
    controller, bot, cfg = _ctrl(tmp_path, heating=False, last_ts=900.0, clock=1000.0)
    res = asyncio.run(controller.apply_output(True, force=True))
    assert res["blocked"] is True
    assert res["retry_after_s"] == 200
    assert bot.calls == []  # never pressed
    assert State.load(cfg.state_file).heating is False


def test_manual_off_always_allowed_even_right_after_change(tmp_path):
    controller, bot, cfg = _ctrl(tmp_path, heating=True, last_ts=999.0, clock=1000.0)
    res = asyncio.run(controller.apply_output(False, force=True))
    assert res.get("changed") is True
    assert bot.calls == [False]


def test_manual_on_allowed_after_min_cycle_elapsed(tmp_path):
    controller, bot, cfg = _ctrl(tmp_path, heating=False, last_ts=600.0, clock=1000.0)  # 400s > 300
    res = asyncio.run(controller.apply_output(True, force=True))
    assert res.get("changed") is True
    assert bot.calls == [True]


def test_first_on_allowed_when_no_prior_action(tmp_path):
    controller, bot, cfg = _ctrl(tmp_path, heating=False, last_ts=0.0, clock=1000.0)
    res = asyncio.run(controller.apply_output(True, force=True))
    assert res.get("changed") is True
    assert bot.calls == [True]
