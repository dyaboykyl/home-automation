"""Centralised logging configuration."""

from __future__ import annotations

import logging
import sys

from .config import LoggingConfig

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(config: LoggingConfig) -> None:
    level = getattr(logging, config.level, logging.INFO)
    handler: logging.Handler
    if config.file:
        handler = logging.FileHandler(config.file)
    else:
        handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # bleak is chatty at DEBUG; keep it one notch quieter than our level.
    logging.getLogger("bleak").setLevel(max(level, logging.INFO))
