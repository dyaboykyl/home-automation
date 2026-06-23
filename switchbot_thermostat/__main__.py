"""Allow `python -m switchbot_thermostat`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
