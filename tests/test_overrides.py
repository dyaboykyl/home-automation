"""Tests for live runtime overrides and effective-settings merging."""

from datetime import datetime

import pytest

from switchbot_thermostat.config import Config
from switchbot_thermostat.controller import effective_settings
from switchbot_thermostat.runtime import Overrides, clear_value, get_value, set_value

BASE = {
    "meter": {"mac": "AA:BB:CC:DD:EE:FF"},
    "bot": {"mac": "11:22:33:44:55:66"},
    "control": {"target_temperature": 21.0, "hysteresis": 0.5},
}


def _cfg(**control):
    d = {**BASE, "control": {**BASE["control"], **control}}
    return Config.from_dict(d)


def test_no_overrides_uses_config_target():
    eff = effective_settings(_cfg(), Overrides(), datetime(2026, 6, 19, 12, 0))
    assert eff.target == 21.0
    assert eff.target_source == "config"
    assert eff.dry_run is False


def test_target_override_wins():
    eff = effective_settings(_cfg(), Overrides(target_temperature=23.5), datetime(2026, 6, 19, 12, 0))
    assert eff.target == 23.5
    assert eff.target_source == "override"


def test_override_beats_schedule():
    cfg = Config.from_dict({
        **BASE,
        "schedule": {"enabled": True, "periods": [{"days": ["fri"], "start": "00:00", "target": 17.0}]},
    })
    now = datetime(2026, 6, 19, 12, 0)  # a Friday
    assert effective_settings(cfg, Overrides(), now).target == 17.0  # schedule
    assert effective_settings(cfg, Overrides(target_temperature=25.0), now).target == 25.0  # override


def test_hysteresis_and_action_overrides():
    eff = effective_settings(_cfg(), Overrides(hysteresis=1.5, action="cool"), datetime(2026, 6, 19, 12, 0))
    assert eff.control.hysteresis == 1.5
    assert eff.control.action == "cool"


def test_dry_run_override():
    eff = effective_settings(_cfg(), Overrides(dry_run=True), datetime(2026, 6, 19, 12, 0))
    assert eff.dry_run is True


def test_set_get_clear_roundtrip():
    ov = Overrides()
    set_value(ov, "target", "22.5")
    assert get_value(ov, "target") == 22.5
    set_value(ov, "dry-run", "on")
    assert get_value(ov, "dry-run") is True
    clear_value(ov, "target")
    assert get_value(ov, "target") is None


def test_set_validates():
    ov = Overrides()
    with pytest.raises(ValueError):
        set_value(ov, "action", "warp")
    with pytest.raises(ValueError):
        set_value(ov, "hysteresis", "0")  # must be > 0
    with pytest.raises(ValueError):
        set_value(ov, "bogus", "1")


def test_persistence(tmp_path):
    path = str(tmp_path / "overrides.json")
    ov = Overrides(target_temperature=20.0, paused=True)
    ov.save(path)
    loaded = Overrides.load(path)
    assert loaded.target_temperature == 20.0
    assert loaded.paused is True
