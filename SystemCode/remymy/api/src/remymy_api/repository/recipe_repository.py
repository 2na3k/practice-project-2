"""The priced food catalog: the price_mapped_nutrients table of remymy.db (DATABASE_PATH), filled from the catalog CSV
(RECIPE_PATH) and read through the ORM (models.RecipeRecord), and the recipes digested against it (DigestedRecipe)."""
from __future__ import annotations

import ast
import csv
import json
import logging
import math
import os
import re
import sys
import threading
from collections import Counter, defaultdict
from collections.abc import Iterable
from functools import cache
from pathlib import Path
from statistics import fmean, median

import nltk
from nltk.corpus import stopwords
from nltk.stem import WordNetLemmatizer
from sqlalchemy import Float, Integer, cast, func, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, contains_eager, joinedload

from ..db import database_path as default_database_path, has_catalog, sqlite_engine
from ..models import (
    RECIPE_ID_PREFIX,
    UNPARSED,
    PythonLiteral,
    RecipeEntry,
    RecipeIngredientEntry,
    RecipeInstructionEntry,
    RecipeRecord,
    RecipeTagEntry,
    UserRecipe,
    ingredient_id,
)
from ..recipe import DigestedRecipe
from ..schemas import Ingredient, Recipe, UserRecipeCreate

logger = logging.getLogger(__name__)

DEFAULT_RECIPE_PATH = "/data/price_mapped_nutrients.csv"
# The Food.com recipes the recommender adds to the catalog when it starts: foodcom_recipe_ratings_ingredients.csv's
# recipes, one row each, as foodcom_map_recipe_review_price.ipynb writes them.
DEFAULT_FOODCOM_RECIPE_PATH = "/data/foodcom_recipes.csv"
# Food.com recipe ids overlap FDC ids, so in the catalog a Food.com recipe's fdc_id is this plus its recipe_id.
FOODCOM_ID_OFFSET = 1_000_000_000
# A Food.com tag on more than this share of its recipes is a category header ("preparation", "course"), not a trait
# of the recipe, and is not kept (as in recipe_clustering_foodcom.ipynb).
FOODCOM_GENERIC_TAG_SHARE = 0.6
# foodcom_recipes.csv nutrient column, per serving (kcal; mg of sodium; g of the rest) -> catalog column, per 100 g.
FOODCOM_NUTRIENTS = {
    "calories (g)": "energy_kcal", "total fat (g)": "fat_g", "sugar (g)": "sugars_g", "sodium (g)": "sodium_mg",
    "protein (g)": "protein_g", "saturated fat (g)": "satfat_g", "carbohydrates (g)": "carb_g",
}

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


def recipe_path() -> Path:
    return Path(os.getenv("RECIPE_PATH", DEFAULT_RECIPE_PATH))


def foodcom_recipe_path() -> Path:
    return Path(os.getenv("FOODCOM_RECIPE_PATH", DEFAULT_FOODCOM_RECIPE_PATH))


def is_foodcom_id(fdc_id: int) -> bool:
    """Whether a catalog fdc_id is a Food.com recipe's (FOODCOM_ID_OFFSET + its recipe_id) rather than an FDC food's."""
    return fdc_id >= FOODCOM_ID_OFFSET


def foodcom_tag(tag: str) -> str:
    """A Food.com tag as a recipe tag label: "60-minutes-or-less" -> "60_minutes_or_less", at most 40 characters."""
    return re.sub(r"[^a-z0-9]+", "_", tag.lower()).strip("_")[:40].rstrip("_")


def floor3(value: float) -> float:
    return math.floor(value * 1000) / 1000


def foodcom_values(row: dict[str, str]) -> dict[str, object] | None:
    """A foodcom_recipes.csv row as RecipeRecord's values, or None unless every ingredient of the recipe is priced.

    Its ingredients are the price_breakdown's (the product spans, with item_prices on the same names), each priced
    by its FairPrice price; an ingredient priced through a broader term ({term: price}) takes that price. adj and verb
    are its ingredients' descriptors, flattened. Nutrients are Food.com's per serving, divided by the serving's grams
    ("1 (129 g)") for per 100 g, floored to 3 decimals; none when the grams are unknown. basket_per_100 is the total
    price per 100 g of the whole recipe (servings x grams). Food.com has no fiber or cholesterol, and no unit prices."""
    if row["price_coverage"] != "full":
        return None
    prices = {name: (sum(value for value in price.values() if value is not None) if isinstance(price, dict) else price)
              for name, price in json.loads(row["price_breakdown"]).items()}
    if not prices or any(price is None for price in prices.values()):
        return None
    match = re.search(r"\((\d+(?:\.\d+)?)\s*g\)", row["serving_size"])
    grams = float(match.group(1)) if match else 0.0
    servings = float(row["servings"]) if row["servings"] else 0.0
    total = round(sum(prices.values()), 2)

    def flattened(column: str) -> list[str]:
        return list(dict.fromkeys(phrase for ingredient in json.loads(row[column]) for phrase in ingredient))

    return {
        "fdc_id": FOODCOM_ID_OFFSET + int(row["recipe_id"]),
        "description": row["name"],
        "product": list(prices),
        "item_prices": {name: {"price": price, "quantity": None} for name, price in prices.items()},
        "item_unit_prices": {}, "item_routes": {}, "matched_variations": {}, "unpriced_items": [],
        "priced_items": len(prices), "total_items": len(prices), "priced_share": 1.0,
        "items_without_unit_price": len(prices),
        "adj": flattened("adj"), "verb": flattened("verb"),
        "total_price": total,
        "basket_per_100": floor3(total / (servings * grams) * 100) if servings > 0 and grams > 0 else 0.0,
        **{column: floor3(float(row[source]) / grams * 100) if grams > 0 else None
           for source, column in FOODCOM_NUTRIENTS.items()},
        "fiber_g": None, "cholesterol_mg": None,
    }


def csv_values(row: dict[str, str]) -> dict[str, object]:
    """A catalog CSV row as RecipeRecord's values. An empty cell is a value FDC does not have, so None."""
    values: dict[str, object] = {}
    for column in RecipeRecord.__table__.columns:
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


def food_ingredient(food: Recipe, price: float | None = None) -> Ingredient:
    """A catalog food as an Ingredient: by default its price is the sum of its items' pack prices, which is its
    total_price when every item is priced; its nutrient values are the ones FDC has a value for."""
    return Ingredient(
        fdc_id=food.fdc_id,
        name=food.description,
        price=sum(item.price for item in food.item_prices.values()) if price is None else price,
        nutrient_values={column: value for column in NUTRIENT_COLUMNS if (value := getattr(food, column)) is not None},
    )


def ingredient_index(foods: Iterable[Recipe]) -> dict[str, Ingredient]:
    """One fully priced food per name key, the first comma-separated part of the description normalized ("Waffles,
    buttermilk, frozen" → "waffle"): of a key's foods, the one whose total_price is nearest their median."""
    groups: dict[str, list[Recipe]] = {}
    for food in sorted(foods, key=lambda food: food.fdc_id):
        key = name_key(food.description.split(",", 1)[0].strip())
        # a Food.com recipe is a dish, not an ingredient a recipe line could be
        if key and food.priced_share == 1 and food.total_price > 0 and not is_foodcom_id(food.fdc_id):
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
        columns = [column.name for column in RecipeRecord.__table__.columns]
        with csv_path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            missing = [column for column in columns if column not in (reader.fieldnames or ())]
            if missing:
                raise ValueError(f"{csv_path} has no column {', '.join(missing)}")
            rows = {int(row["fdc_id"]): row for row in reader}
        RecipeRecord.__table__.create(self.session.connection(), checkfirst=True)
        stored = self.fdc_ids()
        new = [csv_values(row) for fdc_id, row in rows.items() if fdc_id not in stored]
        if new:
            self.session.execute(insert(RecipeRecord), new)
        self.session.commit()
        # an index built before has none of the new foods
        with _ingredient_indexes_lock:
            _ingredient_indexes.pop(self._database, None)
        logger.info("Catalog database %s: %d foods inserted from %s, %d already stored.",
                    self.session.get_bind().engine.url.database, len(new), csv_path, len(rows) - len(new))
        return len(new)

    def recipe_lines(self) -> dict[str, tuple[str, list[str]]]:
        """Every Food.com recipe the migration loaded, by recipe_id: its title and ingredient lines in order, the
        parsed ingredient where the line has one. Empty when the recipe tables are not there."""
        try:
            rows = self.session.execute(text(
                "SELECT r.recipe_id, r.title, COALESCE(i.ingredient_text, i.raw_text) AS line FROM recipe r "
                "JOIN recipe_ingredient i ON i.recipe_id = r.recipe_id "
                "WHERE r.kind = 'openrecipes' ORDER BY r.recipe_id, i.position")).all()
        except OperationalError:
            logger.warning("No recipe tables in %s; only catalog foods can be recommended.", self._database)
            return {}
        recipes: dict[str, tuple[str, list[str]]] = {}
        for recipe_id, title, line in rows:
            recipes.setdefault(recipe_id, (title or recipe_id, []))[1].append(line)
        return recipes

    def foodcom_csv_to_db(self, csv_path: Path) -> int:
        """Insert foodcom_recipes.csv's fully priced recipes (foodcom_values) that the catalog does not hold yet, as
        from_csv_to_db inserts the catalog CSV's foods, and the tags of those that have none yet in recipe_tag (source
        "foodcom", foodcom_tag's labels, the category headers on more than FOODCOM_GENERIC_TAG_SHARE of the file's
        recipes left out). The catalog table must exist. Returns how many recipes went in."""
        # a recipe's steps or description can be longer than the csv module's default field limit
        csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
        with csv_path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            needed = {"recipe_id", "name", "product", "adj", "verb", "price_breakdown", "price_coverage", "servings",
                      "serving_size", *FOODCOM_NUTRIENTS}
            if missing := needed - set(reader.fieldnames or ()):
                raise ValueError(f"{csv_path} has no column {', '.join(sorted(missing))}")
            rows, tags_of, tag_counts, total = [], {}, Counter(), 0
            for row in reader:
                tags = json.loads(row["tags"]) if row.get("tags") else []
                tag_counts.update(set(tags))
                total += 1
                if row["price_coverage"] == "full" and (values := foodcom_values(row)):
                    rows.append(values)
                    tags_of[values["fdc_id"]] = tags
        stored = self.fdc_ids()
        new = [values for values in rows if values["fdc_id"] not in stored]
        if new:
            self.session.execute(insert(RecipeRecord), new)
        if self._has_tag_table():
            self._add_foodcom_tags(tags_of, {tag for tag, count in tag_counts.items()
                                             if count > FOODCOM_GENERIC_TAG_SHARE * total})
        self.session.commit()
        # an index built before has none of the new recipes
        with _ingredient_indexes_lock:
            _ingredient_indexes.pop(self._database, None)
        logger.info("Catalog database %s: %d fully priced Food.com recipes inserted from %s, %d already stored.",
                    self.session.get_bind().engine.url.database, len(new), csv_path, len(rows) - len(new))
        return len(new)

    def _has_tag_table(self) -> bool:
        """Whether the database has recipe_tag: remymy.db does (models.create_tables), a catalog database made on its
        own (init_catalog_database) does not, nor the recipe rows tags refer to."""
        return inspect(self.session.connection()).has_table(RecipeTagEntry.__tablename__)

    def _add_foodcom_tags(self, tags_of: dict[int, list[str]], generic: set[str]) -> None:
        """The Food.com tags of the recipes (by catalog fdc_id) that have none yet, generic ones left out; the recipes
        stored before tags were read get theirs too. Uncommitted."""
        tagged = set(self.session.scalars(
            select(RecipeTagEntry.recipe_id).where(RecipeTagEntry.source == "foodcom").distinct()))
        rows = [{"recipe_id": str(fdc_id), "label": label, "source": "foodcom"}
                for fdc_id, tags in tags_of.items() if str(fdc_id) not in tagged
                for label in dict.fromkeys(foodcom_tag(tag) for tag in tags if tag not in generic) if label]
        if rows:
            self.session.execute(insert(RecipeTagEntry), rows)

    def create_user_recipe(self, body: UserRecipeCreate) -> UserRecipe:
        """Store a recipe a user wrote themselves (POST /api/recipes) as the other recipes are stored: a row of recipe
        (kind 'user', "user-recipe:<n>", n one more than the last), its lines in recipe_ingredient and
        recipe_instruction and its tags in recipe_tag; and its user_recipe row with the owner and the nutrients.
        Returns it as stored, its created_at read back. The caller checks that the user exists. A failed write is rolled
        back; when a save at the same time took the id, the next one is tried."""
        for attempt in range(3):
            recipe_id = f"{RECIPE_ID_PREFIX}{self._last_user_recipe_number() + 1}"
            recipe = UserRecipe(
                recipe_id=recipe_id, user_id=body.user_id, nutrients=body.nutrients,
                recipe=RecipeEntry(
                    recipe_id=recipe_id, title=body.title, quality_status="accepted", kind="user",
                    ingredients=[RecipeIngredientEntry(ingredient_id=ingredient_id(recipe_id, position), position=position,
                                                       raw_text=line, parse_status=UNPARSED)
                                 for position, line in enumerate(body.ingredients, start=1)],
                    instructions=[RecipeInstructionEntry(position=position, text=step)
                                  for position, step in enumerate(body.instructions, start=1)],
                    tags=[RecipeTagEntry(label=tag.label, source=tag.source) for tag in body.tags],
                ),
            )
            try:
                self.session.add(recipe)
                self.session.commit()
                return recipe
            except IntegrityError:
                self.session.rollback()
                if attempt == 2 or self.session.get(RecipeEntry, recipe_id) is None:
                    raise
            except Exception:
                self.session.rollback()
                raise
        raise AssertionError("unreachable")

    def user_recipes(self, user_id: int) -> list[UserRecipe]:
        """A user's own recipes, newest first, each with its row of recipe, its lines and its tags, read in one query:
        user_recipe JOIN recipe, LEFT OUTER JOIN recipe_ingredient, recipe_instruction and recipe_tag. Each recipe
        comes back once (unique), its lines in order and its tags by label."""
        statement = (
            select(UserRecipe)
            .join(UserRecipe.recipe)
            .where(UserRecipe.user_id == user_id)
            .options(contains_eager(UserRecipe.recipe).options(
                joinedload(RecipeEntry.ingredients), joinedload(RecipeEntry.instructions), joinedload(RecipeEntry.tags)))
            .order_by(UserRecipe.created_at.desc(), UserRecipe.recipe_id.desc())
        )
        return list(self.session.scalars(statement).unique())

    def _last_user_recipe_number(self) -> int:
        """The n of the newest "user-recipe:<n>", 0 when there is none."""
        number = cast(func.substr(RecipeEntry.recipe_id, len(RECIPE_ID_PREFIX) + 1), Integer)
        return self.session.scalar(select(func.coalesce(func.max(number), 0)).where(RecipeEntry.kind == "user"))

    def tags(self, recipe_id: str) -> list[str]:
        """The labels of a recipe's tags (any kind of recipe), in label order."""
        return list(self.session.scalars(
            select(RecipeTagEntry.label).where(RecipeTagEntry.recipe_id == recipe_id).order_by(RecipeTagEntry.label)))

    def fdc_ids(self) -> set[int]:
        return set(self.session.scalars(select(RecipeRecord.fdc_id)))

    def all_foods(self) -> list[Recipe]:
        records = self.session.scalars(select(RecipeRecord).order_by(RecipeRecord.fdc_id))
        return [Recipe.model_validate(record) for record in records]

    def foods_by_ids(self, fdc_ids: Iterable[int]) -> list[Recipe]:
        """The foods of those fdc_ids, in the order asked; an id the catalog does not have is left out."""
        wanted = list(dict.fromkeys(fdc_ids))
        if not wanted:
            return []
        records = self.session.scalars(select(RecipeRecord).where(RecipeRecord.fdc_id.in_(wanted)))
        by_id = {record.fdc_id: record for record in records}
        return [Recipe.model_validate(by_id[fdc_id]) for fdc_id in wanted if fdc_id in by_id]

    def food(self, fdc_id: int) -> Recipe | None:
        record = self.session.get(RecipeRecord, fdc_id)
        return None if record is None else Recipe.model_validate(record)

    def search(self, query: str, limit: int = 20, offset: int = 0) -> list[Recipe]:
        """Foods whose description contains the query, case-insensitively, in fdc_id order."""
        records = self.session.scalars(
            select(RecipeRecord)
            .where(RecipeRecord.description.ilike(f"%{query.strip()}%"))
            .order_by(RecipeRecord.fdc_id).limit(limit).offset(offset)
        )
        return [Recipe.model_validate(record) for record in records]

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

    def digest(self, recipe_id: str, lines: Iterable[str]) -> DigestedRecipe:
        """A recipe's ingredient lines → the DigestedRecipe the recommender reads. Each line's name key is matched to the
        food the ingredient index holds for it, a key once however many lines share it; a line no priced food
        matches is left out. The recipe's nutrient values are the mean of its matched foods'."""
        index = self._ingredients()
        keys = dict.fromkeys(name_key(line) for line in lines)
        ingredients = [index[key] for key in keys if key in index]
        nutrients: dict[str, list[float]] = defaultdict(list)
        for ingredient in ingredients:
            for name, value in ingredient.nutrient_values.items():
                nutrients[name].append(value)
        return DigestedRecipe(recipe_id, ingredients, {name: fmean(values) for name, values in sorted(nutrients.items())})

    def digest_food(self, food_id: str) -> DigestedRecipe:
        """A catalog food the user rated itself, such as a recommended grocery, by its fdc_id: the DigestedRecipe whose one
        ingredient is the food, or none when the catalog does not have it."""
        food = self.food(int(food_id))
        if food is None:
            return DigestedRecipe(food_id, [])
        ingredient = food_ingredient(food)
        return DigestedRecipe(food_id, [ingredient], ingredient.nutrient_values)


def init_catalog_database(database_path: Path | None = None, csv_path: Path | None = None) -> bool:
    """Bring the database's catalog up to date with the catalog CSV, as the API does at startup: its table is created
    where missing, and the CSV's foods it does not hold yet are inserted. Without a CSV the catalog stored is used as
    it is. Returns whether there is a catalog; without one, the routes that need it answer 503."""
    database_path = database_path or default_database_path()
    csv_path = csv_path or recipe_path()
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


def add_foodcom_recipes(session: Session, csv_path: Path | None = None) -> int:
    """Add the fully priced Food.com recipes to the catalog, as the recommender does when it starts, after startup filled
    the catalog from its CSV. Without a Food.com CSV the catalog stays as it is. Returns how many went in."""
    csv_path = csv_path or foodcom_recipe_path()
    if not csv_path.is_file():
        logger.info("No Food.com recipes at %s; the catalog keeps only its CSV's foods.", csv_path)
        return 0
    return RecipeRepository(session).foodcom_csv_to_db(csv_path)
