from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import URL, Engine, create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session

logger = logging.getLogger(__name__)

# remymy.db, the one database: recipes and FoodData Central, the catalog, the users and all they keep (schema.py).
DEFAULT_DATABASE_PATH = "/state/remymy.db"
REQUIRED_TABLES = {"recipe", "recipe_ingredient", "recipe_instruction"}


class Base(DeclarativeBase):
    """The declarative base of the SQLAlchemy ORM models (models.py) of remymy.db."""


def database_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH))


def _enable_foreign_keys(dbapi_connection: sqlite3.Connection, _connection_record: object) -> None:
    # SQLite enforces REFERENCES ... ON DELETE CASCADE only on connections that ask, as schema.connect does
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


@lru_cache(maxsize=16)
def sqlite_engine(path: Path) -> Engine:
    """The SQLAlchemy engine of the SQLite database at path: one per path, so tests can point a database elsewhere."""
    # URL.create takes the path as it is (no URL parsing of spaces or other characters); check_same_thread is off as
    # FastAPI may open a session in one worker thread and close it in another
    engine = create_engine(URL.create("sqlite", database=str(path)), connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _enable_foreign_keys)
    return engine


def database_engine() -> Engine:
    """The engine of remymy.db at DATABASE_PATH."""
    return sqlite_engine(database_path())


def has_catalog(session: Session) -> bool:
    """Whether the database holds a catalog: startup builds it from the catalog CSV when there is one."""
    try:
        return session.execute(text("SELECT 1 FROM price_mapped_nutrients LIMIT 1")).first() is not None
    except OperationalError:
        # a database startup has not set up yet
        return False


def get_db() -> Iterator[Session]:
    """A session on the database for one request (a FastAPI dependency). The route commits what it writes; anything
    left uncommitted is rolled back when the session closes."""
    with Session(database_engine()) as session:
        yield session


def get_catalog_db() -> Iterator[Session | None]:
    """A session on the database for one request's catalog, or None when it holds no catalog. A missing file is not
    opened, as SQLite would create it empty."""
    path = database_path()
    if not path.is_file():
        yield None
        return
    with Session(sqlite_engine(path)) as session:
        yield session if has_catalog(session) else None


def open_database() -> sqlite3.Connection:
    """Open the database for the recipe routes' SQL, or raise a generic, non-leaky 503 when it is missing or has no
    recipe tables. The reason goes to the log, not the caller."""

    path = database_path()
    if not path.is_file():
        logger.warning("No database at %s.", path)
        raise HTTPException(status_code=503, detail="Recipe data is unavailable.")
    try:
        # Quote the resolved path so spaces, query characters, and other path bytes cannot alter the SQLite URI;
        # mode=rw, as a missing file is not to be created empty.
        uri_path = quote(str(path.resolve()), safe="/")
        connection = sqlite3.connect(f"file:{uri_path}?mode=rw", uri=True)
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
            logger.warning("Database %s lacks table(s) %s.", path, ", ".join(sorted(REQUIRED_TABLES - tables)))
            raise HTTPException(status_code=503, detail="Recipe data is unavailable.")
        return connection
    except HTTPException:
        raise
    except (OSError, sqlite3.Error):
        logger.exception("Could not open the database %s.", path)
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
