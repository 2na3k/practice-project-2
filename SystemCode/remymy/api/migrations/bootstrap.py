"""Make sure remymy.db holds the recipe and FoodData Central data, loading it when it does not.

remymy.db is the API's one database: these tables sit next to the users and everything they keep, so loading never
replaces the file. With no arguments this command is idempotent: a database with recipes is accepted as it is. One
without them takes the old recipe database's (LEGACY_FOOD_DATABASE_PATH) when there is one, or else an ingest of the
sources, which is only attempted when both recipe and FoodData Central source paths are supplied explicitly through
flags or environment variables. Either way the data is merged in with upserts (remymy_api.schema.merge_food_data), so
a row already there is updated, never deleted, and the saves and history that refer to it stay.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

try:
    from ingest_food_data import main as ingest_main
except ModuleNotFoundError:  # Supports `python -m migrations.bootstrap`.
    from .ingest_food_data import main as ingest_main

# ingest_food_data has put the API's src on sys.path if it was not on PYTHONPATH
from remymy_api.schema import FOOD_SCHEMA, connect, has_food_data, merge_food_data, read_only

logger = logging.getLogger("migrations.bootstrap")
# The same line format and LOG_LEVEL as the API's (remymy_api.logs).
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

DEFAULT_DATABASE_PATH = "/state/remymy.db"
DEFAULT_LEGACY_DATABASE_PATH = "/data/remymy-food.db"
REQUIRED_TABLES = {"recipe", "recipe_ingredient", "recipe_instruction"}


class DatabaseValidationError(RuntimeError):
    pass


def validate_database(path: Path, *, recipes_required: bool = True, source: bool = False) -> None:
    """Check the SQLite database at path is sound and has the recipe tables, and, unless told not to, recipes. A source
    (the old recipe database, an ingest's output) is opened read-only, as its mount may be."""
    if not path.is_file():
        raise DatabaseValidationError(f"Database does not exist: {path}")
    try:
        with (sqlite3.connect(read_only(path), uri=True) if source else sqlite3.connect(path)) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()[0]
            if str(result).lower() != "ok":
                raise DatabaseValidationError("SQLite quick_check did not return ok")
            placeholders = ",".join("?" for _ in REQUIRED_TABLES)
            tables = {
                row[0]
                for row in connection.execute(
                    f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({placeholders})",
                    tuple(REQUIRED_TABLES),
                )
            }
            if tables != REQUIRED_TABLES:
                missing = ", ".join(sorted(REQUIRED_TABLES - tables))
                raise DatabaseValidationError(f"required recipe tables are missing: {missing}")
            if recipes_required and connection.execute("SELECT 1 FROM recipe LIMIT 1").fetchone() is None:
                raise DatabaseValidationError("the recipe table is empty")
    except sqlite3.Error as exc:
        raise DatabaseValidationError(f"SQLite validation of {path} failed: {exc}") from exc


def holds_recipes(path: Path) -> bool:
    """Whether remymy.db at path is there and holds the Food.com recipes already."""
    if not path.is_file():
        return False
    connection = connect(path)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        return "recipe" in tables and has_food_data(connection)
    finally:
        connection.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-path",
        type=Path,
        default=Path(os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH)),
        help="remymy.db, the API's database; defaults to DATABASE_PATH",
    )
    parser.add_argument(
        "--legacy-database",
        type=Path,
        default=Path(os.getenv("LEGACY_FOOD_DATABASE_PATH", DEFAULT_LEGACY_DATABASE_PATH)),
        help="The recipe database of before remymy.db, merged in when remymy.db has no recipes; "
        "defaults to LEGACY_FOOD_DATABASE_PATH",
    )
    parser.add_argument(
        "--recipes",
        type=Path,
        default=Path(os.environ["RECIPES_SOURCE"]) if os.getenv("RECIPES_SOURCE") else None,
        help="Open Recipes .db/.csv; required when there are no recipes to merge",
    )
    parser.add_argument(
        "--fdc-dir",
        type=Path,
        default=Path(os.environ["FDC_SOURCE_DIR"]) if os.getenv("FDC_SOURCE_DIR") else None,
        help="FoodData Central CSV directory; required when there are no recipes to merge",
    )
    parser.add_argument(
        "--fdc-release",
        default=os.getenv("FDC_RELEASE", "unspecified"),
        help="FoodData Central release label",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only check remymy.db holds the recipes; never merge or ingest",
    )
    return parser.parse_args(argv)


def merge_into(database_path: Path, source: Path) -> None:
    """Merge the food tables of the database at source into remymy.db, creating them there where missing."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(database_path)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(FOOD_SCHEMA)
        merge_food_data(connection, source)
    finally:
        connection.close()


def ingest_into(args: argparse.Namespace) -> None:
    """Ingest the sources into a temporary database next to remymy.db, then merge that into it."""
    if args.recipes is None or args.fdc_dir is None:
        raise DatabaseValidationError(
            "The database has no recipes. Provide both --recipes and --fdc-dir "
            "or set RECIPES_SOURCE and FDC_SOURCE_DIR to ingest them."
        )
    if not args.recipes.is_file():
        raise DatabaseValidationError(f"Recipe source does not exist: {args.recipes}")
    if not args.fdc_dir.is_dir():
        raise DatabaseValidationError(f"FDC source directory does not exist: {args.fdc_dir}")

    args.database_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{args.database_path.name}.ingest.",
            suffix=".tmp",
            dir=args.database_path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
        logger.info("Ingesting %s and %s into %s.", args.recipes, args.fdc_dir, temporary_path)
        ingest_main(
            [
                "--recipes",
                str(args.recipes),
                "--fdc-dir",
                str(args.fdc_dir),
                "--fdc-release",
                args.fdc_release,
                "--output",
                str(temporary_path),
                "--replace",
            ]
        )
        validate_database(temporary_path, source=True)
        merge_into(args.database_path, temporary_path)
    finally:
        if temporary_path is not None:
            for path in (temporary_path, Path(f"{temporary_path}-wal"), Path(f"{temporary_path}-shm")):
                if path.exists():
                    path.unlink()


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    logging.basicConfig(level=level if isinstance(logging.getLevelName(level), int) else "INFO", format=LOG_FORMAT)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv or sys.argv[1:])
    try:
        if holds_recipes(args.database_path):
            validate_database(args.database_path)
            logger.info("Database holds the recipes: %s", args.database_path)
            return 0
        if args.validate_only:
            raise DatabaseValidationError(f"Database has no recipes: {args.database_path}")
        if args.legacy_database.is_file():
            validate_database(args.legacy_database, source=True)
            logger.info("Merging the recipe database %s into %s.", args.legacy_database, args.database_path)
            merge_into(args.database_path, args.legacy_database)
        else:
            ingest_into(args)
        validate_database(args.database_path)
        logger.info("Database loaded and validated: %s", args.database_path)
        return 0
    except (DatabaseValidationError, OSError, sqlite3.Error, SystemExit) as exc:
        logger.error("Bootstrap failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
