"""Remymy recipe API."""

from .logs import configure_logging

# Before any submodule runs, so everything they log is in this format.
configure_logging()
