"""Tests for manual believed-state correction."""

from switchbot_thermostat.config import Config
from switchbot_thermostat.cli import _cmd_state
from switchbot_thermostat.state import State

CFG = Config.from_dict({
    "meter": {"mac": "AA:BB:CC:DD:EE:FF"},
    "bot": {"mac": "11:22:33:44:55:66"},
})


def _cfg(tmp_path):
    # Point the state file at a temp location.
    cfg = Config.from_dict({
        "meter": {"mac": "AA:BB:CC:DD:EE:FF"},
        "bot": {"mac": "11:22:33:44:55:66"},
        "state_file": str(tmp_path / "state.json"),
    })
    return cfg


def test_set_state_on_off(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    assert _cmd_state(cfg, "on") == 0
    assert State.load(cfg.state_file).heating is True
    assert _cmd_state(cfg, "off") == 0
    assert State.load(cfg.state_file).heating is False


def test_set_state_clears_short_cycle_timer(tmp_path):
    cfg = _cfg(tmp_path)
    State(heating=False, last_action_ts=123456.0).save(cfg.state_file)
    _cmd_state(cfg, "on")
    loaded = State.load(cfg.state_file)
    assert loaded.heating is True
    assert loaded.last_action_ts == 0.0  # correction frees the next actuation


def test_show_state_does_not_change_it(tmp_path):
    cfg = _cfg(tmp_path)
    State(heating=True, last_action_ts=999.0).save(cfg.state_file)
    assert _cmd_state(cfg, None) == 0
    loaded = State.load(cfg.state_file)
    assert loaded.heating is True
    assert loaded.last_action_ts == 999.0
