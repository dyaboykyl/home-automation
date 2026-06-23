"""Tests for SwitchBot Meter advertisement decoding."""

from switchbot_thermostat import ble


def test_decode_positive_temperature():
    # integer byte 0x95 -> sign bit set (positive) + 0x15 = 21 whole degrees,
    # decimal byte 0x03 -> 0.3, so 21.3C. byte[2]=0x64 -> battery 100%,
    # byte[5]=0x32 -> humidity 50%.
    data = bytes([0x54, 0x00, 0x64, 0x03, 0x95, 0x32])
    reading = ble.decode_meter_service_data(data)
    assert reading is not None
    assert round(reading.temperature_c, 1) == 21.3
    assert reading.humidity == 50
    assert reading.battery == 100


def test_decode_negative_temperature():
    # integer byte without the sign bit -> negative temperature.
    data = bytes([0x54, 0x00, 0x50, 0x05, 0x05, 0x28])
    reading = ble.decode_meter_service_data(data)
    assert reading is not None
    assert round(reading.temperature_c, 1) == -5.5


def test_decode_too_short_returns_none():
    assert ble.decode_meter_service_data(bytes([0x54, 0x00])) is None
    assert ble.decode_meter_service_data(b"") is None


def test_parse_advertisement_prefers_service_data():
    class Adv:
        service_data = {
            "0000fd3d-0000-1000-8000-00805f9b34fb": bytes([0x54, 0x00, 0x64, 0x00, 0x96, 0x2D])
        }
        manufacturer_data = {}
        rssi = -60

    reading = ble.parse_advertisement(Adv())
    assert reading is not None
    assert round(reading.temperature_c, 1) == 22.0
    assert reading.rssi == -60


def test_parse_advertisement_manufacturer_fallback():
    class Adv:
        service_data = {}
        # offsets 9/10/11 carry temp/humidity for outdoor meters.
        manufacturer_data = {
            ble.SWITCHBOT_COMPANY_ID: bytes(
                [0, 1, 2, 3, 4, 5, 6, 7, 8, 0x02, 0x93, 0x2A]
            )
        }
        rssi = -70

    reading = ble.parse_advertisement(Adv())
    assert reading is not None
    assert round(reading.temperature_c, 1) == 19.2
    assert reading.humidity == 42


def test_bot_command_encoding_without_password():
    assert ble._encode_bot_command(ble._BOT_PRESS, None) == bytes([0x57, 0x01, 0x00])
    assert ble._encode_bot_command(ble._BOT_ON, "") == bytes([0x57, 0x01, 0x01])


class _FakeChar:
    def __init__(self, uuid, properties):
        self.uuid = uuid
        self.properties = properties


class _FakeService:
    def __init__(self, characteristics):
        self.characteristics = characteristics


class _FakeClient:
    def __init__(self, services):
        self.services = services


def test_find_command_char_matches_9fb8_revision():
    # The Bot revision in the wild reports 9fb8, not the documented 9fb9.
    client = _FakeClient([
        _FakeService([_FakeChar("cba20003-224d-11e6-9fb8-0002a5d5c51b", ["notify"])]),
        _FakeService([_FakeChar("cba20002-224d-11e6-9fb8-0002a5d5c51b", ["write-without-response", "write"])]),
    ])
    char = ble._find_command_char(client)
    assert char is not None
    assert char.uuid.startswith("cba20002")


def test_find_command_char_none_when_absent():
    client = _FakeClient([_FakeService([_FakeChar("00002a00-0000-1000-8000-00805f9b34fb", ["read"])])])
    assert ble._find_command_char(client) is None
