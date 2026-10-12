from __future__ import annotations

import ast
import csv
import logging
import os
import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from contextlib import closing
from functools import cache, cached_property
from pathlib import Path
from statistics import fmean, median

import nltk
from nltk.corpus import stopwords
from nltk.stem import WordNetLemmatizer

from ..recipe import Recipe
from ..schemas import CatalogFood, Ingredient

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

# The nutrients a matched ingredient reports, for its recipe's nutrient values and the buying profile.
NUTRIENT_COLUMNS = (
    "energy_kcal", "protein_g", "fat_g", "satfat_g", "carb_g", "sugars_g", "fiber_g", "sodium_mg", "cholesterol_mg",
)

# map_recipe_nutrients.ipynb keeps these of NLTK's English stop words: the single letters and the negations.
KEPT_STOP_WORDS = {"a", "d", "i", "m", "no", "nor", "not", "o", "s", "t", "y"}


def catalog_path() -> Path:
    return Path(os.getenv("CATALOG_PATH", DEFAULT_CATALOG_PATH))


def catalog_database_path() -> Path:
    return Path(os.getenv("CATALOG_DATABASE_PATH", DEFAULT_CATALOG_DATABASE_PATH))


def food_from_row(row: sqlite3.Row) -> CatalogFood:
    values = dict(row)
    for column in LITERAL_COLUMNS:
        values[column] = ast.literal_eval(values[column])
    return CatalogFood(**values)


@cache
def text_tools() -> tuple[set[str], WordNetLemmatizer]:
    """The stop words name_key drops and the lemmatizer it singularizes with, loaded the first time a name is
    normalized. The NLTK data they read is downloaded then if it is missing; the API image ships it."""
    lemmatizer = WordNetLemmatizer()
    try:
        words = stopwords.words("english")
        lemmatizer.lemmatize("tomatoes", "n")
    except LookupError:
        for package in ("stopwords", "wordnet", "omw-1.4"):
            nltk.download(package, quiet=True)
        words = stopwords.words("english")
    return set(words) - KEPT_STOP_WORDS, lemmatizer


def name_key(text: str) -> str:
    """An ingredient line or a food's name, normalized as map_recipe_nutrients.ipynb normalizes them to match:
    lowercased, "(...)" asides, punctuation and stop words dropped, then each word singularized ("Tomatoes (ripe),
    chopped" → "tomato chopped"). The notebook singularizes with its own plural rules; WordNet's noun lemma stands in
    for them, as in the reranker."""
    stop_words, lemmatizer = text_tools()
    text = re.sub(r"\(.*?\)", " ", text.lower())
    text = re.sub(r"[^a-z0-9%\s]", " ", text)
    words = [word for word in text.split() if word not in stop_words and word.strip("%")]
    return " ".join(lemmatizer.lemmatize(word, "n") for word in words)


def is_food_id(item_id: str) -> bool:
    """Whether an id a user rated is a catalog food's fdc_id ("169286") rather than a recipe's ("openrecipes:5210")."""
    return item_id.isascii() and item_id.isdigit()


def food_ingredient(food: CatalogFood, price: float | None = None) -> Ingredient:
    """A catalog food as an Ingredient: by default its price is the sum of its items' pack prices, which is its
    total_price when every item is priced; its nutrient values are the ones FDC has a value for."""
    return Ingredient(
        fdc_id=food.fdc_id,
        name=food.description,
        price=sum(item.price for item in food.item_prices.values()) if price is None else price,
        nutrient_values={column: value for column in NUTRIENT_COLUMNS if (value := getattr(food, column)) is not None},
    )


def ingredient_index(foods: Iterable[CatalogFood]) -> dict[str, Ingredient]:
    """One fully priced food per name key, the first comma-separated part of the description normalized ("Waffles,
    buttermilk, frozen" → "waffle"): of a key's foods, the one whose total_price is nearest their median."""
    groups: dict[str, list[CatalogFood]] = {}
    for food in sorted(foods, key=lambda food: food.fdc_id):
        key = name_key(food.description.split(",", 1)[0].strip())
        if key and food.priced_share == 1 and food.total_price > 0:
            groups.setdefault(key, []).append(food)
    index = {}
    for key, group in groups.items():
        middle = median(food.total_price for food in group)
        # min keeps the first of a tie, the lowest fdc_id
        food = min(group, key=lambda food: abs(food.total_price - middle))
        index[key] = food_ingredient(food, price=food.total_price)
    return index


class RecipeRepository:
    """The priced food catalog, read from its SQLite database, and the Recipes digested against it.

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
                logger.info("No catalog CSV at %s; using the catalog database %s as it is.",
                            self.csv_path, self.database_path)
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
        logger.info("Catalog database %s: %d foods inserted from %s, %d already stored.",
                    self.database_path, len(values), self.csv_path, len(rows) - len(values))
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

    @cached_property
    def _ingredients(self) -> dict[str, Ingredient]:
        """The catalog's ingredient_index, built the first time it is needed and kept."""
        return ingredient_index(self.all_foods())

    def load_ingredient_index(self) -> int:
        """Build the ingredient index now, so the first recipe digested does not wait for it; returns its name keys'
        count. Building it again does nothing."""
        return len(self._ingredients)

    def digest(self, recipe_id: str, lines: Iterable[str]) -> Recipe:
        """A recipe's ingredient lines → the Recipe the recommender reads. Each line's name key is matched to the
        food the ingredient index holds for it, a key once however many lines share it; a line no priced food
        matches is left out. The recipe's nutrient values are the mean of its matched foods'."""
        keys = dict.fromkeys(name_key(line) for line in lines)
        ingredients = [self._ingredients[key] for key in keys if key in self._ingredients]
        nutrients: dict[str, list[float]] = defaultdict(list)
        for ingredient in ingredients:
            for name, value in ingredient.nutrient_values.items():
                nutrients[name].append(value)
        return Recipe(recipe_id, ingredients, {name: fmean(values) for name, values in sorted(nutrients.items())})

    def digest_food(self, food_id: str) -> Recipe:
        """A catalog food the user rated itself, such as a recommended grocery, by its fdc_id: the Recipe whose one
        ingredient is the food, or none when the catalog does not have it."""
        food = self.food(int(food_id))
        if food is None:
            return Recipe(food_id, [])
        ingredient = food_ingredient(food)
        return Recipe(food_id, [ingredient], ingredient.nutrient_values)


# The one repository the app shares, built from CATALOG_PATH / CATALOG_DATABASE_PATH when this module is first
# imported. None when there is neither a catalog database nor a CSV to build it from, so that importing it never
# fails: the recipe and user routes do not need the catalog.
recipe_repository: RecipeRepository | None
try:
    recipe_repository = RecipeRepository()
except FileNotFoundError as error:
    logger.warning("%s", error)
    recipe_repository = None
