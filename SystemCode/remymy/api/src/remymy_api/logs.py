"""Logging for the whole API: one line per record, with time, level and module, at the level LOG_LEVEL names."""
from __future__ import annotations

import logging
import os

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DEFAULT_LEVEL = "INFO"


def log_level() -> str:
    return os.getenv("LOG_LEVEL", DEFAULT_LEVEL).strip().upper() or DEFAULT_LEVEL


def configure_logging() -> None:
    """Send the remymy_api loggers' records to stderr. uvicorn keeps its own handlers for its access and server logs.

    An unknown LOG_LEVEL falls back to INFO with a warning, rather than stopping the API from starting."""
    level = log_level()
    known = isinstance(logging.getLevelName(level), int)
    logging.basicConfig(level=level if known else DEFAULT_LEVEL, format=FORMAT)
    if not known:
        logging.getLogger(__name__).warning("Unknown LOG_LEVEL %r; logging at %s.", level, DEFAULT_LEVEL)
