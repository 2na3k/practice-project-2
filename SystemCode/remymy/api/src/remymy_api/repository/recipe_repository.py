from __future__ import annotations

import ast
import csv
import logging
import os
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

from ..schemas import CatalogFood

logger = logging.getLogger(__name__)

DEFAULT_CATALOG_PATH = "/data/price_mapped_nutrients.csv"
DEFAULT_CATALOG_DATABASE_PATH = "/state/remymy-catalog.db"
TABLE = "price_mapped_nutrients"

# price_mapped_nutrients.csv column for column, in file order. Prices are SGD; nutrients are per 100 g, NULL where
# FDC has no value.
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  fdc_id                   INTEGER PRIMARY KEY,
  description              TEXT NOT NULL,
  product                  TEXT NOT NULL,  -- list: the food's ingredient spans
  item_prices              TEXT NOT NULL,  -- dict: span -> {{'price': pack price, 'quantity': pack size(s) in g / ml}}
  item_unit_prices         TEXT NOT NULL,  -- dict: span -> price per 100 g / 100 ml
  item_routes              TEXT NOT NULL,  -- dict: span -> 'variation' | 'average' | 'term crawl'
  matched_variations       TEXT NOT NULL,  -- dict: span -> the brand / variant words it matched
  unpriced_items           TEXT NOT NULL,  -- list: spans no listing priced
  priced_items             INTEGER NOT NULL CHECK (priced_items >= 1),
  total_items              INTEGER NOT NULL CHECK (total_items >= priced_items),
  priced_share             REAL NOT NULL CHECK (priced_share > 0 AND priced_share <= 1),
  items_without_unit_price INTEGER NOT NULL CHECK (items_without_unit_price BETWEEN 0 AND priced_items),
  adj                      TEXT NOT NULL,  -- list
  verb                     TEXT NOT NULL,  -- list
  total_price              REAL NOT NULL CHECK (total_price >= 0),     -- 0 unless every item is priced
  basket_per_100           REAL NOT NULL CHECK (basket_per_100 >= 0),  -- 0 unless every item is priced
  protein_g                REAL,
  fat_g                    REAL,
  carb_g                   REAL,
  energy_kcal              REAL,
  fiber_g                  REAL,
  sodium_mg                REAL,
  cholesterol_mg           REAL,
  satfat_g                 REAL,
  sugars_g                 REAL
);
"""

LITERAL_COLUMNS = (
    "product", "item_prices", "item_unit_prices", "item_routes", "matched_variations", "unpriced_items", "adj", "verb",
)
CONVERTERS = {"INTEGER": int, "REAL": float, "TEXT": str}


def catalog_path() -> Path:
    return Path(os.getenv("CATALOG_PATH", DEFAULT_CATALOG_PATH))


def catalog_database_path() -> Path:
    return Path(os.getenv("CATALOG_DATABASE_PATH", DEFAULT_CATALOG_DATABASE_PATH))


def food_from_row(row: sqlite3.Row) -> CatalogFood:
    values = dict(row)
    for column in LITERAL_COLUMNS:
        values[column] = ast.literal_eval(values[column])
    return CatalogFood(**values)

class RecipeRepository:
    """The priced food catalog, read from its SQLite database.

    Creating one brings the database up to date with the catalog CSV first: a missing database is created, and the
    CSV's foods whose fdc_id the table does not hold yet are injected, so a grown catalog adds just its new foods.
    Without a CSV an existing database is used as it is."""

    def __init__(self, database_path: Path | None = None, csv_path: Path | None = None) -> None:
        self.database_path = Path(database_path) if database_path else catalog_database_path()
        self.csv_path = Path(csv_path) if csv_path else catalog_path()
        self.injected = self.inject()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def inject(self) -> int:
        """Insert the CSV's foods the table does not hold yet; returns how many went in."""
        if not self.csv_path.is_file():
            if self.database_path.is_file():
                return 0
            raise FileNotFoundError(
                f"No catalog database at {self.database_path}, and no catalog CSV at {self.csv_path} to build it from."
            )
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.executescript(SCHEMA)
            types = {row["name"]: row["type"] for row in connection.execute(f"PRAGMA table_info({TABLE})")}
            stored = self._fdc_ids(connection)
            with self.csv_path.open(newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                missing = [column for column in types if column not in (reader.fieldnames or ())]
                if missing:
                    raise ValueError(f"{self.csv_path} has no column {', '.join(missing)}")
                rows = {int(row["fdc_id"]): row for row in reader}
            new_ids = rows.keys() - stored
            # an empty cell is a value FDC does not have, so NULL rather than ''
            values = [
                tuple(None if row[column] == "" else CONVERTERS[kind](row[column]) for column, kind in types.items())
                for fdc_id, row in rows.items()
                if fdc_id in new_ids
            ]
            with connection:
                connection.executemany(
                    f"INSERT INTO {TABLE} ({', '.join(types)}) VALUES ({', '.join('?' * len(types))})", values
                )
        return len(values)

    @staticmethod
    def _fdc_ids(connection: sqlite3.Connection) -> set[int]:
        return {row[0] for row in connection.execute(f"SELECT DISTINCT fdc_id FROM {TABLE}")}

    def fdc_ids(self) -> set[int]:
        with closing(self.connect()) as connection:
            return self._fdc_ids(connection)

    def all_foods(self) -> list[CatalogFood]:
        with closing(self.connect()) as connection:
            rows = connection.execute(f"SELECT * FROM {TABLE} ORDER BY fdc_id").fetchall()
        return [food_from_row(row) for row in rows]

    def foods_by_ids(self, fdc_ids: Iterable[int]) -> list[CatalogFood]:
        """The foods of those fdc_ids, in the order asked; an id the catalog does not have is left out."""
        wanted = list(dict.fromkeys(fdc_ids))
        if not wanted:
            return []
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM {TABLE} WHERE fdc_id IN ({', '.join('?' * len(wanted))})", wanted
            ).fetchall()
        by_id = {row["fdc_id"]: row for row in rows}
        return [food_from_row(by_id[fdc_id]) for fdc_id in wanted if fdc_id in by_id]

    def food(self, fdc_id: int) -> CatalogFood | None:
        found = self.foods_by_ids([fdc_id])
        return found[0] if found else None

    def search(self, query: str, limit: int = 20, offset: int = 0) -> list[CatalogFood]:
        """Foods whose description contains the query, case-insensitively, in fdc_id order."""
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM {TABLE} WHERE description LIKE ? COLLATE NOCASE ORDER BY fdc_id LIMIT ? OFFSET ?",
                (f"%{query.strip()}%", limit, offset),
            ).fetchall()
        return [food_from_row(row) for row in rows]


# The one repository the app shares, built from CATALOG_PATH / CATALOG_DATABASE_PATH when this module is first
# imported. None when there is neither a catalog database nor a CSV to build it from, so that importing it never
# fails: the recipe and user routes do not need the catalog.
recipe_repository: RecipeRepository | None
try:
    recipe_repository = RecipeRepository()
except FileNotFoundError as error:
    logger.warning("%s", error)
    recipe_repository = None
