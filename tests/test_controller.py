"""Tests for the hysteresis + safety decision logic."""

from switchbot_thermostat.config import ControlConfig, SafetyConfig
from switchbot_thermostat.controller import decide_heating

CONTROL = ControlConfig(target_temperature=21.0, hysteresis=0.5, action="heat")
SAFETY = SafetyConfig(min_temperature=5.0, max_temperature=30.0)


def decide(temp, prev, control=CONTROL, safety=SAFETY):
    return decide_heating(temp, control.target_temperature, prev, control, safety).desired_heating


def test_heat_on_below_band():
    assert decide(20.0, prev=False) is True


def test_heat_off_above_band():
    assert decide(22.0, prev=True) is False


def test_deadband_holds_previous_state():
    # Within target ± hysteresis the previous decision is kept (no chatter).
    assert decide(21.0, prev=True) is True
    assert decide(21.0, prev=False) is False


def test_frost_protection_forces_heat():
    assert decide(3.0, prev=False) is True


def test_overheat_cutoff_forces_off():
    assert decide(31.0, prev=True) is False


def test_cooling_mode_inverts():
    cool = ControlConfig(target_temperature=24.0, hysteresis=0.5, action="cool")
    # Hot -> cooling on; cold -> cooling off.
    assert decide(25.0, prev=False, control=cool) is True
    assert decide(23.0, prev=True, control=cool) is False


def test_disabled_safety_thresholds():
    safety = SafetyConfig(min_temperature=None, max_temperature=None)
    # With no frost protection, a very cold reading still just follows hysteresis.
    assert decide(3.0, prev=False, safety=safety) is True  # below band -> heat on anyway
    assert decide(100.0, prev=True, safety=safety) is False  # above band -> off
