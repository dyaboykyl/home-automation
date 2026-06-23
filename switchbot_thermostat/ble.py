"""Low-level Bluetooth LE access to SwitchBot devices via ``bleak``.

Two responsibilities:

* Passively scan for a SwitchBot **Meter** advertisement and decode the
  temperature / humidity / battery it broadcasts (no connection required).
* Connect to a SwitchBot **Bot** (a.k.a. WoHand button pusher) and send a
  press / on / off command over GATT.

The advertisement decoding is implemented directly (rather than depending on
the fast-moving ``pySwitchbot`` package) so the only runtime dependency is
``bleak``. The byte layouts are covered by unit tests in ``tests/``.
"""

from __future__ import annotations

import asyncio
import logging
import zlib

from .models import Reading

_LOGGER = logging.getLogger(__name__)

# SwitchBot company identifier in BLE manufacturer data.
SWITCHBOT_COMPANY_ID = 0x0969

# Service-data UUIDs used by SwitchBot sensors across firmware generations.
METER_SERVICE_UUIDS = (
    "0000fd3d-0000-1000-8000-00805f9b34fb",  # current Meter / Meter Plus
    "cba20d00-224d-11e6-9fb9-0002a5d5c51b",  # legacy
)

# Bot (WoHand) GATT handles.
BOT_CMD_CHAR = "cba20002-224d-11e6-9fb9-0002a5d5c51b"
BOT_NOTIFY_CHAR = "cba20003-224d-11e6-9fb9-0002a5d5c51b"

# Bot command payloads (no password). Magic byte 0x57, group 0x01.
_BOT_PRESS = bytes([0x57, 0x01, 0x00])
_BOT_ON = bytes([0x57, 0x01, 0x01])
_BOT_OFF = bytes([0x57, 0x01, 0x02])


# --------------------------------------------------------------------------- #
# Meter advertisement decoding                                                #
# --------------------------------------------------------------------------- #
def _decode_temp(decimal_byte: int, integer_byte: int) -> float:
    """Decode SwitchBot's two-byte temperature encoding into degrees C.

    ``integer_byte`` holds the whole degrees in bits 0-6, with bit 7 as the
    sign (1 = positive). ``decimal_byte`` holds the 0.1C digit in bits 0-3.
    """
    sign = 1 if integer_byte & 0x80 else -1
    return sign * ((integer_byte & 0x7F) + (decimal_byte & 0x0F) / 10)


def decode_meter_service_data(data: bytes) -> Reading | None:
    """Decode the 6+ byte service-data payload of a Meter / Meter Plus."""
    if data is None or len(data) < 6:
        return None
    battery = data[2] & 0x7F
    temperature_c = _decode_temp(data[3], data[4])
    humidity = data[5] & 0x7F
    return Reading(temperature_c=temperature_c, humidity=humidity, battery=battery)


def decode_meter_manufacturer_data(mfr: bytes, battery: int | None) -> Reading | None:
    """Decode temperature from manufacturer data (Outdoor/IO Meter layout).

    For these devices the temperature lives at offsets 9/10/11 of the
    manufacturer payload, while battery is only present in the service data.
    """
    if mfr is None or len(mfr) < 12:
        return None
    temperature_c = _decode_temp(mfr[9], mfr[10])
    humidity = mfr[11] & 0x7F
    return Reading(temperature_c=temperature_c, humidity=humidity, battery=battery)


def parse_advertisement(adv) -> Reading | None:
    """Extract a :class:`Reading` from a bleak ``AdvertisementData`` object.

    Tries service data first (covers the common Meter), then falls back to
    manufacturer data (Outdoor Meter). Returns ``None`` if nothing parseable.
    """
    service_data = getattr(adv, "service_data", {}) or {}
    battery: int | None = None
    for uuid in METER_SERVICE_UUIDS:
        raw = service_data.get(uuid)
        if not raw:
            continue
        reading = decode_meter_service_data(bytes(raw))
        if reading is not None:
            battery = reading.battery
            # Service data carries temperature for indoor Meters; prefer it.
            return Reading(
                temperature_c=reading.temperature_c,
                humidity=reading.humidity,
                battery=reading.battery,
                rssi=getattr(adv, "rssi", None),
            )

    mfr_map = getattr(adv, "manufacturer_data", {}) or {}
    mfr = mfr_map.get(SWITCHBOT_COMPANY_ID)
    if mfr:
        reading = decode_meter_manufacturer_data(bytes(mfr), battery)
        if reading is not None:
            return Reading(
                temperature_c=reading.temperature_c,
                humidity=reading.humidity,
                battery=reading.battery,
                rssi=getattr(adv, "rssi", None),
            )
    return None


# --------------------------------------------------------------------------- #
# Bleak-backed I/O                                                            #
# --------------------------------------------------------------------------- #
async def read_meter(mac: str, timeout: float) -> Reading | None:
    """Scan for ``mac`` and return its first parseable Meter reading.

    Uses a passive detection callback and stops as soon as the target device's
    advertisement is decoded, or after ``timeout`` seconds.
    """
    from bleak import BleakScanner  # imported lazily so tests/CLI help work off-Pi

    target = mac.lower()
    found: dict[str, Reading] = {}
    done = asyncio.Event()

    def _callback(device, adv) -> None:
        if device.address.lower() != target:
            return
        reading = parse_advertisement(adv)
        if reading is not None:
            found["reading"] = reading
            done.set()

    scanner = BleakScanner(detection_callback=_callback)
    await scanner.start()
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        _LOGGER.warning("No advertisement from meter %s within %.0fs", mac, timeout)
    finally:
        await scanner.stop()
    return found.get("reading")


def _encode_bot_command(base: bytes, password: str | None) -> bytes:
    """Return the BLE payload for a Bot command, applying a password if set.

    Without a password the well-known 3-byte command is used. With a password
    the 4-byte CRC32 of the password is inserted after a ``0x57 0x11`` header
    (experimental; most Bots have no password configured).
    """
    if not password:
        return base
    crc = zlib.crc32(password.encode()) & 0xFFFFFFFF
    action = base[2]  # 0x00 press / 0x01 on / 0x02 off
    return bytes([0x57, 0x11, action]) + crc.to_bytes(4, "big")


async def send_bot_command(
    mac: str,
    command: str,
    *,
    password: str | None = None,
    retries: int = 3,
    timeout: float = 20.0,
) -> None:
    """Connect to the Bot and send ``command`` in {"press", "on", "off"}.

    Retries on transient BLE errors with a short backoff. Raises the last
    exception if all attempts fail.
    """
    from bleak import BleakClient

    base = {"press": _BOT_PRESS, "on": _BOT_ON, "off": _BOT_OFF}[command]
    payload = _encode_bot_command(base, password)

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            async with BleakClient(mac, timeout=timeout) as client:
                await client.write_gatt_char(BOT_CMD_CHAR, payload, response=True)
            _LOGGER.info("Bot %s: sent %s command", mac, command)
            return
        except Exception as exc:  # bleak raises a wide range of errors
            last_exc = exc
            _LOGGER.warning("Bot %s: %s attempt %d/%d failed: %s", mac, command, attempt, retries, exc)
            if attempt < retries:
                await asyncio.sleep(2 * attempt)
    assert last_exc is not None
    raise last_exc


async def discover(timeout: float = 10.0) -> list[dict]:
    """Scan and return likely SwitchBot devices (address, name, rssi, reading)."""
    from bleak import BleakScanner

    results: dict[str, dict] = {}

    def _callback(device, adv) -> None:
        service_data = getattr(adv, "service_data", {}) or {}
        mfr_map = getattr(adv, "manufacturer_data", {}) or {}
        is_switchbot = SWITCHBOT_COMPANY_ID in mfr_map or any(
            u in service_data for u in METER_SERVICE_UUIDS
        )
        if not is_switchbot:
            return
        reading = parse_advertisement(adv)
        results[device.address] = {
            "address": device.address,
            "name": device.name or adv.local_name,
            "rssi": getattr(adv, "rssi", None),
            "temperature_c": reading.temperature_c if reading else None,
            "looks_like": "meter" if reading else "bot/other",
        }

    scanner = BleakScanner(detection_callback=_callback)
    await scanner.start()
    try:
        await asyncio.sleep(timeout)
    finally:
        await scanner.stop()
    return list(results.values())
