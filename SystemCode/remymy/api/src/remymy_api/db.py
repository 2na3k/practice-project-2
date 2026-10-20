from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session

logger = logging.getLogger(__name__)

DEFAULT_DATABASE_PATH = "/data/remymy-food.db"
DEFAULT_USER_DATABASE_PATH = "/data/remymy-users.db"
REQUIRED_TABLES = {"recipe", "recipe_ingredient", "recipe_instruction"}


class Base(DeclarativeBase):
    """The declarative base of the SQLAlchemy ORM models (models.py), all of them tables of the users database."""


def user_database_path() -> Path:
    return Path(os.getenv("USER_DATABASE_PATH", DEFAULT_USER_DATABASE_PATH))


def _enable_foreign_keys(dbapi_connection: sqlite3.Connection, _connection_record: object) -> None:
    # SQLite enforces REFERENCES ... ON DELETE CASCADE only on connections that ask, as open_user_database does
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


@lru_cache(maxsize=8)
def _users_engine(path: str) -> Engine:
    # URL.create takes the path as it is (no URL parsing of spaces or other characters); check_same_thread is off as
    # FastAPI may open a session in one worker thread and close it in another
    engine = create_engine(URL.create("sqlite", database=path), connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _enable_foreign_keys)
    return engine


def users_engine() -> Engine:
    """The SQLAlchemy engine of the users database at USER_DATABASE_PATH: one per path, so tests can point it
    elsewhere."""
    return _users_engine(str(user_database_path()))


def get_db() -> Iterator[Session]:
    """A session on the users database for one request (a FastAPI dependency). The route commits what it writes;
    anything left uncommitted is rolled back when the session closes."""
    with Session(users_engine()) as session:
        yield session


def database_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH))


def open_database() -> sqlite3.Connection:
    """Open a valid recipe database or raise a generic, non-leaky 503. The reason goes to the log, not the caller."""

    path = database_path()
    if not path.is_file():
        logger.warning("No recipe database at %s.", path)
        raise HTTPException(status_code=503, detail="Recipe data is unavailable.")
    try:
        # The API database is mounted read-only and is produced completely
        # before the API starts. Immutable read-only mode prevents SQLite from
        # attempting to create or update sidecar -shm/-wal files. Quote the
        # resolved path so spaces, query characters, and other path bytes
        # cannot alter the SQLite URI.
        uri_path = quote(str(path.resolve()), safe="/")
        connection = sqlite3.connect(
            f"file:{uri_path}?mode=ro&immutable=1",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in REQUIRED_TABLES)
        tables = {
            row[0]
            for row in connection.execute(
                f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({placeholders})",
                tuple(REQUIRED_TABLES),
            )
        }
        if tables != REQUIRED_TABLES:
            connection.close()
            logger.warning("Recipe database %s lacks table(s) %s.", path, ", ".join(sorted(REQUIRED_TABLES - tables)))
            raise HTTPException(status_code=503, detail="Recipe data is unavailable.")
        return connection
    except HTTPException:
        raise
    except (OSError, sqlite3.Error):
        logger.exception("Could not open the recipe database %s.", path)
        raise HTTPException(status_code=503, detail="Recipe data is unavailable.")


def close_database(connection: sqlite3.Connection) -> None:
    try:
        connection.close()
    except sqlite3.Error:
        pass


def split_preview(value: str | None) -> list[str]:
    if not value:
        return []
    return [part for part in value.split("\n") if part]
