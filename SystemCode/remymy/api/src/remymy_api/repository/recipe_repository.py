"""The priced food catalog: the price_mapped_nutrients table of remymy.db (DATABASE_PATH), filled from the catalog CSV
(CATALOG_PATH) and read through the ORM (models.CatalogFoodRecord), and the Recipes digested against it."""
from __future__ import annotations

import ast
import csv
import logging
import os
import re
import threading
from collections import defaultdict
from collections.abc import Iterable
from functools import cache
from pathlib import Path
from statistics import fmean, median

import nltk
from nltk.corpus import stopwords
from nltk.stem import WordNetLemmatizer
from sqlalchemy import Float, Integer, insert, select
from sqlalchemy.orm import Session

from ..db import database_path as default_database_path, has_catalog, sqlite_engine
from ..models import CatalogFoodRecord, PythonLiteral
from ..recipe import Recipe
from ..schemas import CatalogFood, Ingredient

logger = logging.getLogger(__name__)

DEFAULT_CATALOG_PATH = "/data/price_mapped_nutrients.csv"

# The nutrients a matched ingredient reports, for its recipe's nutrient values and the buying profile.
NUTRIENT_COLUMNS = (
    "energy_kcal", "protein_g", "fat_g", "satfat_g", "carb_g", "sugars_g", "fiber_g", "sodium_mg", "cholesterol_mg",
)

# map_recipe_nutrients.ipynb keeps these of NLTK's English stop words: the single letters and the negations.
KEPT_STOP_WORDS = {"a", "d", "i", "m", "no", "nor", "not", "o", "s", "t", "y"}

# The ingredient index of each catalog database, by its URL: built once from its foods, then shared by the repositories
# every request makes on it.
_ingredient_indexes: dict[str, dict[str, Ingredient]] = {}
_ingredient_indexes_lock = threading.Lock()


def catalog_path() -> Path:
    return Path(os.getenv("CATALOG_PATH", DEFAULT_CATALOG_PATH))


def csv_values(row: dict[str, str]) -> dict[str, object]:
    """A catalog CSV row as CatalogFoodRecord's values. An empty cell is a value FDC does not have, so None."""
    values: dict[str, object] = {}
    for column in CatalogFoodRecord.__table__.columns:
        text = row[column.name]
        if text == "":
            values[column.name] = None
        elif isinstance(column.type, PythonLiteral):
            values[column.name] = ast.literal_eval(text)
        elif isinstance(column.type, Integer):
            values[column.name] = int(text)
        elif isinstance(column.type, Float):
            values[column.name] = float(text)
        else:
            values[column.name] = text
    return values


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
    """The priced food catalog in its database, through the ORM, and the Recipes digested against it.

    One is made per request on that request's session (dependencies.get_recipe_repository). The ingredient index the
    digests match against is built once per catalog database and shared by them all."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @property
    def _database(self) -> str:
        return str(self.session.get_bind().engine.url)

    def from_csv_to_db(self, csv_path: Path) -> int:
        """Create the catalog table where it is missing and insert the CSV's foods whose fdc_id it does not hold yet,
        so a grown catalog adds just its new foods (a stored food is left as it is). Returns how many went in."""
        columns = [column.name for column in CatalogFoodRecord.__table__.columns]
        with csv_path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            missing = [column for column in columns if column not in (reader.fieldnames or ())]
            if missing:
                raise ValueError(f"{csv_path} has no column {', '.join(missing)}")
            rows = {int(row["fdc_id"]): row for row in reader}
        CatalogFoodRecord.__table__.create(self.session.connection(), checkfirst=True)
        stored = self.fdc_ids()
        new = [csv_values(row) for fdc_id, row in rows.items() if fdc_id not in stored]
        if new:
            self.session.execute(insert(CatalogFoodRecord), new)
        self.session.commit()
        # an index built before has none of the new foods
        with _ingredient_indexes_lock:
            _ingredient_indexes.pop(self._database, None)
        logger.info("Catalog database %s: %d foods inserted from %s, %d already stored.",
                    self.session.get_bind().engine.url.database, len(new), csv_path, len(rows) - len(new))
        return len(new)

    def fdc_ids(self) -> set[int]:
        return set(self.session.scalars(select(CatalogFoodRecord.fdc_id)))

    def all_foods(self) -> list[CatalogFood]:
        records = self.session.scalars(select(CatalogFoodRecord).order_by(CatalogFoodRecord.fdc_id))
        return [CatalogFood.model_validate(record) for record in records]

    def foods_by_ids(self, fdc_ids: Iterable[int]) -> list[CatalogFood]:
        """The foods of those fdc_ids, in the order asked; an id the catalog does not have is left out."""
        wanted = list(dict.fromkeys(fdc_ids))
        if not wanted:
            return []
        records = self.session.scalars(select(CatalogFoodRecord).where(CatalogFoodRecord.fdc_id.in_(wanted)))
        by_id = {record.fdc_id: record for record in records}
        return [CatalogFood.model_validate(by_id[fdc_id]) for fdc_id in wanted if fdc_id in by_id]

    def food(self, fdc_id: int) -> CatalogFood | None:
        record = self.session.get(CatalogFoodRecord, fdc_id)
        return None if record is None else CatalogFood.model_validate(record)

    def search(self, query: str, limit: int = 20, offset: int = 0) -> list[CatalogFood]:
        """Foods whose description contains the query, case-insensitively, in fdc_id order."""
        records = self.session.scalars(
            select(CatalogFoodRecord)
            .where(CatalogFoodRecord.description.ilike(f"%{query.strip()}%"))
            .order_by(CatalogFoodRecord.fdc_id).limit(limit).offset(offset)
        )
        return [CatalogFood.model_validate(record) for record in records]

    def _ingredients(self) -> dict[str, Ingredient]:
        """This catalog database's ingredient_index, built from its foods the first time it is needed and kept."""
        with _ingredient_indexes_lock:
            index = _ingredient_indexes.get(self._database)
            if index is None:
                index = _ingredient_indexes[self._database] = ingredient_index(self.all_foods())
        return index

    def load_ingredient_index(self) -> int:
        """Build the ingredient index now, so the first recipe digested does not wait for it; returns its name keys'
        count. Building it again does nothing."""
        return len(self._ingredients())

    def digest(self, recipe_id: str, lines: Iterable[str]) -> Recipe:
        """A recipe's ingredient lines → the Recipe the recommender reads. Each line's name key is matched to the
        food the ingredient index holds for it, a key once however many lines share it; a line no priced food
        matches is left out. The recipe's nutrient values are the mean of its matched foods'."""
        index = self._ingredients()
        keys = dict.fromkeys(name_key(line) for line in lines)
        ingredients = [index[key] for key in keys if key in index]
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


def init_catalog_database(database_path: Path | None = None, csv_path: Path | None = None) -> bool:
    """Bring the database's catalog up to date with the catalog CSV, as the API does at startup: its table is created
    where missing, and the CSV's foods it does not hold yet are inserted. Without a CSV the catalog stored is used as
    it is. Returns whether there is a catalog; without one, the routes that need it answer 503."""
    database_path = database_path or default_database_path()
    csv_path = csv_path or catalog_path()
    if not csv_path.is_file():
        if database_path.is_file():
            with Session(sqlite_engine(database_path)) as session:
                if has_catalog(session):
                    logger.info("No catalog CSV at %s; using the catalog in %s as it is.", csv_path, database_path)
                    return True
        logger.warning("No catalog in %s, and no catalog CSV at %s to fill it from.", database_path, csv_path)
        return False
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with Session(sqlite_engine(database_path)) as session:
        RecipeRepository(session).from_csv_to_db(csv_path)
    return True
