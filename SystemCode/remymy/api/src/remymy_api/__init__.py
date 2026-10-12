"""Remymy recipe API."""

from .logs import configure_logging

# Before any submodule runs: the shared recipe_repository already logs when it is created on import.
configure_logging()
