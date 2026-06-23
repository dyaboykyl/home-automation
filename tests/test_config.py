"""Tests for config loading and validation."""

import pytest

from switchbot_thermostat.config import Config, ConfigError, load_config

VALID = {
    "meter": {"mac": "AA:BB:CC:DD:EE:FF"},
    "bot": {"mac": "11:22:33:44:55:66", "mode": "toggle"},
    "control": {"target_temperature": 21, "hysteresis": 0.5},
}


def test_valid_config_parses():
    cfg = Config.from_dict(VALID)
    assert cfg.meter.mac == "AA:BB:CC:DD:EE:FF"
    assert cfg.bot.mode == "toggle"
    assert cfg.control.target_temperature == 21
    # Defaults are filled in.
    assert cfg.control.poll_interval == 60.0
    assert cfg.safety.min_temperature == 5.0


def test_missing_meter_raises():
    with pytest.raises(ConfigError, match="meter"):
        Config.from_dict({"bot": {"mac": "11:22:33:44:55:66"}})


def test_bad_mac_raises():
    with pytest.raises(ConfigError, match="MAC"):
        Config.from_dict({"meter": {"mac": "nope"}, "bot": {"mac": "11:22:33:44:55:66"}})


def test_bad_mode_raises():
    bad = {**VALID, "bot": {"mac": "11:22:33:44:55:66", "mode": "spin"}}
    with pytest.raises(ConfigError, match="bot.mode"):
        Config.from_dict(bad)


def test_zero_hysteresis_raises():
    bad = {**VALID, "control": {"hysteresis": 0}}
    with pytest.raises(ConfigError, match="hysteresis"):
        Config.from_dict(bad)


def test_load_config_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/path/config.yaml")
