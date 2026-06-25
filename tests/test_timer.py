"""Tests for the auto-off timer."""

import asyncio
from datetime import datetime

from switchbot_thermostat.cli import _parse_timer_arg
from switchbot_thermostat.config import Config
from switchbot_thermostat.controller import Controller, build_status, off_at_epoch
from switchbot_thermostat.runtime import Overrides
from switchbot_thermostat.state import State


class FakeBot:
    def __init__(self):
        self.calls = []

    async def apply(self, on):
        self.calls.append(on)


def _ctrl(tmp_path, heating=True, off_timer_at=0.0, clock=1000.0):
    cfg = Config.from_dict({
        "meter": {"mac": "AA:BB:CC:DD:EE:FF"},
        "bot": {"mac": "11:22:33:44:55:66"},
        "state_file": str(tmp_path / "s.json"),
    })
    State(heating=heating, off_timer_at=off_timer_at).save(cfg.state_file)
    bot = FakeBot()
    controller = Controller(cfg, None, bot, State(), clock=lambda: clock)
    return controller, bot, cfg


def test_set_timer_in_computes_epoch(tmp_path):
    controller, _, cfg = _ctrl(tmp_path, clock=1000.0)
    epoch = controller.set_timer_in(30)  # 30 minutes
    assert epoch == 1000.0 + 30 * 60
    assert State.load(cfg.state_file).off_timer_at == epoch


def test_off_at_epoch_next_occurrence():
    now = datetime(2026, 6, 24, 23, 0)  # 11pm
    # 06:30 has already passed today -> should be tomorrow.
    epoch = off_at_epoch(now, 6, 30)
    target = datetime.fromtimestamp(epoch)
    assert (target.hour, target.minute) == (6, 30)
    assert target.day == 25


def test_check_timer_fires_and_turns_off(tmp_path):
    # Timer was due at t=999, clock is 1000 -> should fire.
    controller, bot, cfg = _ctrl(tmp_path, heating=True, off_timer_at=999.0, clock=1000.0)
    asyncio.run(controller.check_timer())
    assert bot.calls == [False]  # turned off
    state = State.load(cfg.state_file)
    assert state.heating is False
    assert state.off_timer_at == 0.0  # cleared


def test_check_timer_not_yet_due(tmp_path):
    controller, bot, cfg = _ctrl(tmp_path, heating=True, off_timer_at=2000.0, clock=1000.0)
    asyncio.run(controller.check_timer())
    assert bot.calls == []  # not fired
    assert State.load(cfg.state_file).off_timer_at == 2000.0


def test_turning_off_cancels_timer(tmp_path):
    controller, bot, cfg = _ctrl(tmp_path, heating=True, off_timer_at=5000.0, clock=1000.0)
    asyncio.run(controller.apply_output(False, force=True))
    assert State.load(cfg.state_file).off_timer_at == 0.0  # cancelled by turning off


def test_build_status_includes_timer(tmp_path):
    cfg = Config.from_dict({"meter": {"mac": "AA:BB:CC:DD:EE:FF"}, "bot": {"mac": "11:22:33:44:55:66"}})
    now = datetime(2026, 6, 24, 12, 0)
    off_at = now.timestamp() + 1800  # 30 min out
    status = build_status(cfg, Overrides(), State(heating=True, off_timer_at=off_at), None, None, now)
    assert status["off_timer_at"] == off_at
    assert 1790 <= status["timer_remaining_s"] <= 1800


def test_parse_timer_arg():
    assert _parse_timer_arg("30m") == {"minutes": 30.0}
    assert _parse_timer_arg("2h") == {"minutes": 120.0}
    assert _parse_timer_arg("90") == {"minutes": 90.0}
    assert _parse_timer_arg("22:30") == {"at": "22:30"}
    assert _parse_timer_arg("off") == {"clear": True}
    assert _parse_timer_arg("garbage") is None
