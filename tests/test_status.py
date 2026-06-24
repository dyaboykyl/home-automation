"""Tests for the web status snapshot and WebConfig."""

from datetime import datetime

from switchbot_thermostat.config import Config, WebConfig
from switchbot_thermostat.controller import build_status
from switchbot_thermostat.models import Reading
from switchbot_thermostat.runtime import Overrides
from switchbot_thermostat.state import State

NOW = datetime(2026, 6, 24, 12, 0)


def _cfg(**root):
    base = {"meter": {"mac": "AA:BB:CC:DD:EE:FF"}, "bot": {"mac": "11:22:33:44:55:66"}}
    return Config.from_dict({**base, **root})


def test_web_config_defaults():
    cfg = _cfg()
    assert cfg.web.enabled is True
    assert cfg.web.host == "0.0.0.0"
    assert cfg.web.port == 8080
    assert cfg.web.auth_token is None


def test_web_config_overrides():
    cfg = _cfg(web={"enabled": False, "port": 9000, "auth_token": "secret"})
    assert cfg.web.enabled is False
    assert cfg.web.port == 9000
    assert cfg.web.auth_token == "secret"


def test_build_status_basic_cool_mode():
    cfg = _cfg(control={"target_temperature": 22.0, "hysteresis": 0.5, "action": "cool"})
    reading = Reading(temperature_c=26.0, humidity=40, battery=95)
    status = build_status(cfg, Overrides(), State(heating=True), reading, 12.0, NOW)
    assert status["temperature"] == 26.0
    assert status["action"] == "cool"
    assert status["believed"] is True
    assert status["desired"] is True  # 26 > 22+0.5 -> cooling on
    assert status["reading_age"] == 12.0
    assert status["humidity"] == 40
    assert status["target"] == 22.0


def test_build_status_reflects_override_target():
    cfg = _cfg(control={"target_temperature": 21.0})
    status = build_status(cfg, Overrides(target_temperature=18.0), State(), Reading(20.0), 1.0, NOW)
    assert status["target"] == 18.0
    assert status["target_source"] == "override"


def test_build_status_no_reading():
    cfg = _cfg()
    status = build_status(cfg, Overrides(), State(), None, None, NOW)
    assert status["temperature"] is None
    assert status["desired"] is None
    assert status["reading_age"] is None
    assert status["target"] is not None  # target is still known without a reading


def test_build_status_paused_and_dry_run():
    cfg = _cfg(safety={"dry_run": True})
    status = build_status(cfg, Overrides(paused=True), State(), Reading(19.0), 5.0, NOW)
    assert status["paused"] is True
    assert status["dry_run"] is True
