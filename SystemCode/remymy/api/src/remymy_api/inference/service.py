"""The inference service the recommend route calls, over the catalog RecipeRepository serves.

The pipeline's items are the catalog's foods and the Food.com recipes, each recipe digested (RecipeRepository.digest)
into the catalog foods its ingredient lines match. A user's History holds whether they liked each item they rated: a
food or recipe the pipeline holds goes into its history as itself, a recipe it does not (one the user wrote) as the
catalog foods it digests to, each with the like as a rating and its date."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from statistics import median

import numpy as np
from sqlalchemy.orm import Session

from ..db import database_engine
from ..history import History
from ..recipe import DigestedRecipe
from ..repository.recipe_repository import FOODCOM_ID_OFFSET, RecipeRepository, is_food_id
from ..schemas import Recipe, RecipeDetail
from .model_inference_score_recommend import (CATALOG_NUTRIENTS, DEFAULT_ARTIFACTS, DEFAULT_INDEX_DIR, DIET_COLUMNS,
                                              DIET_EXCLUDES, TEXT_COLUMNS, RetrievalPipeline)
from .classification_service import lookup_path
from .food_query import NUTRIENT_COLS, EmptyQuery, TermExtractor, build_item
from .model_inference_recipe_clustering import DEFAULT_ARTIFACTS as CLUSTERING_ARTIFACTS
from .model_inference_recipe_clustering import RecipeClusterer
from .singleton import Singleton

logger = logging.getLogger(__name__)

# The model reads Food.com's 0-5 ratings, of which training counted 3 to 5 as liked: a like goes in as the top
# rating, a dislike as the bottom one.
LIKED_RATING, DISLIKED_RATING = 5, 0


def artifact_dir() -> Path:
    return Path(os.getenv("ARTIFACT_DIR", str(DEFAULT_ARTIFACTS)))


def index_dir() -> Path:
    return Path(os.getenv("INDEX_DIR", str(DEFAULT_INDEX_DIR)))


def clustering_dir() -> Path:
    return Path(os.getenv("CLUSTERING_ARTIFACT_DIR", str(CLUSTERING_ARTIFACTS)))


def nutrient_medians(foods: list[Recipe]) -> dict[str, float]:
    """The catalog's median of each nutrient column, for an item without a value for it."""
    medians = {}
    for column in CATALOG_NUTRIENTS.values():
        known = [value for food in foods if (value := getattr(food, column)) is not None]
        medians[column] = median(known) if known else float("nan")
    return medians


def catalog_recipes(foods: list[Recipe]) -> list[dict]:
    """The catalog's foods → recipes in the reranker's input format, in the order given.

    product / adj / verb are already NER output, so each phrase becomes one ingredient of "entities" and no tagging
    is needed. total_price is 0 whenever an item is unpriced, so price is the sum of the items' pack prices instead
    (as in recipe_clustering.ipynb). A nutrient FDC has no value for (sugars_g for 13% of foods) takes the catalog
    median."""
    medians = nutrient_medians(foods)
    recipes = []
    for food in foods:
        nutrients = {}
        for key, column in CATALOG_NUTRIENTS.items():
            value = getattr(food, column)
            nutrients[key] = float(medians[column] if value is None else value)
        recipes.append({"fdc_id": food.fdc_id, "name": food.description,
                        "price": float(sum(item.price for item in food.item_prices.values())),
                        "nutrients": nutrients,
                        "entities": {field: [[phrase] for phrase in getattr(food, field)] for field in TEXT_COLUMNS},
                        # the food's diet columns, when the catalog has them (None screens the food by its words)
                        "special_diet": {column: getattr(food, column, None) for column in DIET_COLUMNS.values()}})
    return recipes


def openrecipe_items(repository: RecipeRepository, foods: list[Recipe]) -> list[dict]:
    """The Food.com recipes → items in the reranker's input format, as catalog_recipes makes the foods'.

    A recipe is digested into the catalog foods its ingredient lines match: its product / adj / verb terms are theirs,
    its price the sum of their prices and its nutrients their mean, as in training, where a recipe's price summed its
    ingredients' and its words were its ingredients'. Its diets and allergens are screened on every ingredient line,
    matched or not, so a line no food matches ("bacon") still rules one out. A recipe no line of which matches is left out: there is
    nothing to embed it by."""
    food_by_id = {food.fdc_id: food for food in foods}
    medians = nutrient_medians(foods)
    items = []
    for recipe_id, (title, lines) in repository.recipe_lines().items():
        recipe = repository.digest(recipe_id, lines)
        if not recipe.ingredients:
            continue
        matched = [food_by_id[ingredient.fdc_id] for ingredient in recipe.ingredients]
        words = set(re.findall(r"[a-z]+", " ".join([title, *lines]).lower()))
        items.append({"fdc_id": recipe_id, "name": title,
                      "price": float(sum(ingredient.price for ingredient in recipe.ingredients)),
                      "nutrients": {key: float(recipe.nutrient_values.get(column, medians[column]))
                                    for key, column in CATALOG_NUTRIENTS.items()},
                      "entities": {field: [[phrase] for phrase in dict.fromkeys(phrase for food in matched
                                                                                 for phrase in getattr(food, field))]
                                   for field in TEXT_COLUMNS},
                      "special_diet": {DIET_COLUMNS[diet]: not words.intersection(terms)
                                       for diet, terms in DIET_EXCLUDES.items()},
                      # every line, matched or not, for the allergen screen
                      "ingredients": lines})
    return items


class Recommender(Singleton):
    """The retrieval pipeline, built from the catalog, one per process: every Recommender() is the same instance.
    Startup constructs it on the database's catalog; a route's Recommender() is then that loaded instance."""

    def _load(self, repository: RecipeRepository | None = None, pipeline: RetrievalPipeline | None = None) -> None:
        """The given pipeline, or one over the repository's catalog (the database's when no repository is given).
        The repository's session is only read during the load."""
        if pipeline is None:
            if repository is None:
                with Session(database_engine()) as session:
                    pipeline = self._build(RecipeRepository(session))
            else:
                pipeline = self._build(repository)
        self.pipeline = pipeline

    @staticmethod
    def _build(repository: RecipeRepository) -> RetrievalPipeline:
        foods = repository.all_foods()
        # the ingredient index first: the recipes are digested against it (and the first recommendation does not
        # wait for it)
        keys = repository.load_ingredient_index()
        started = time.perf_counter()
        recipes = openrecipe_items(repository, foods)
        logger.info("Digested %d Food.com recipes into items in %.1fs.", len(recipes), time.perf_counter() - started)
        pipeline = RetrievalPipeline.from_recipes(catalog_recipes(foods) + recipes, artifact_dir(), index_dir())
        logger.info("Recommender loaded: %d catalog foods and %d recipes, %d ingredient keys.", len(foods),
                    len(recipes), keys)
        return pipeline

    def recommend(self, history: History, recipes: Mapping[str, DigestedRecipe], top_k: int,
                  candidates: int = 100, preferences: Mapping | None = None) -> list[dict]:
        """The pipeline's recommendations from the history's foods; the preferences' diet and budget constrain which
        foods its retrieval may return."""
        # Preparing the history: an item the pipeline holds as itself, any other recipe as the foods it digests to
        history_foods = []
        for entry in history:
            rated = {"rating": LIKED_RATING if entry.like else DISLIKED_RATING,
                     "date": entry.timestamp.date().isoformat()}
            if self.pipeline.holds(entry.recipe_id):
                history_foods.append({"fdc_id": entry.recipe_id, **rated})
            else:
                history_foods.extend({"fdc_id": ingredient.fdc_id, **rated}
                                     for ingredient in recipes[entry.recipe_id].ingredients)
        results = []
        if history_foods:
            try:
                trace = logger.debug if logger.isEnabledFor(logging.DEBUG) else None
                results = self.pipeline.recommend({"history": history_foods, "preferences": dict(preferences or {})},
                                                  n_candidates=candidates, top_k=top_k, log=trace)
            except Exception as e:
                logger.error(f"Error encountered when recommending: {e}")

        logger.debug("%d history recipes matched %d catalog foods; %d recommended.", len(history), len(history_foods),
                     len(results))
        return [
            {key: result[key] for key in ("fdc_id", "name", "price", "p_like", "similarity")}
            for result in results
        ]

def catalog_query(food: Recipe, tags: list[str], unpriced_band: float) -> dict:
    """A catalog recipe as a clustering query, by what the catalog stores of it, as the model's training read its
    recipes: its product / adj / verb terms, nutrients per 100 g, its tags, and its price as the interval the notebook
    makes (the priced ingredients' sum, give or take unpriced_band per unpriced one)."""
    priced = sum(item.price for item in food.item_prices.values())
    band = unpriced_band * len(food.unpriced_items)
    return {"description": food.description, "product": food.product, "adj": food.adj, "verb": food.verb,
            "nutrients": {column: getattr(food, column) for column in NUTRIENT_COLS}, "price": None,
            "price_interval": [max(priced - band, 0.0), priced + band], "tags": tags}


class Clusterer(Singleton):
    """The recipe clusterer, one per process: every Clusterer() is the same instance. The first construction, at
    startup, loads and warms up every model a query reads, so a query only infers; later ones ignore their arguments."""

    def _load(self,
              lookup: Path | None = None) -> None:
        """Loads what is not given: the clustering bundle and the encoder for a term it has not seen, and GLiNER and
        stanza for a query's terms, downloading any not cached yet; then warms the loaded models up."""
        started = time.perf_counter()
        recipe_clusterer = RecipeClusterer(clustering_dir(), lookup_path())
        logger.info("Recipe clusterer loaded: %d clusters over %d training %s.",
                    len(recipe_clusterer.bundle["cluster_names"]), len(recipe_clusterer.bundle["train_labels"]),
                    "Food.com recipes" if recipe_clusterer.ids_are_foodcom else "FDC foods")
        recipe_clusterer.load_encoder()
        self.clusterer = recipe_clusterer
        self.extractor = TermExtractor().load()
        self.lookup = lookup or lookup_path()
        # One query at a time, as for the classifier: neither the extractor's models nor the encoder are meant to be
        # shared across threads.
        self.lock = threading.Lock()
        logger.info("Clusterer models loaded in %.1fs.", time.perf_counter() - started)
        self.warm_up()

    def warm_up(self) -> None:
        """One query through the whole pipeline, so the first person to open a recipe does not wait for what only a
        first call does: numba compiling UMAP's transform and the nearest-neighbour search."""
        started = time.perf_counter()
        logger.info("Warming up clusterer")
        with self.lock:
            # an invented product term, so the encoder of unseen terms runs too
            item = build_item("Tomato soup, canned", self.extractor, terms={"product": ["remymy warm up"]},
                              lookup=self.lookup)
            self.clusterer.predict(item, k=1)
        logger.info("Clusterer warmed up in %.1fs.", time.perf_counter() - started)

    def catalog_ids(self) -> np.ndarray:
        """The catalog fdc_id of each training recipe, in training order: a Food.com recipe's is FOODCOM_ID_OFFSET
        plus its recipe_id (as add_foodcom_recipes stores it), an FDC food's its fdc_id."""
        ids = self.clusterer.train_ids
        return ids + FOODCOM_ID_OFFSET if self.clusterer.ids_are_foodcom else ids

    def cluster(self, recipe: RecipeDetail, top_k: int, catalog: RecipeRepository) -> list[Recipe]:
        """The top_k catalog recipes nearest a recipe in its cluster, the recipe itself left out. A catalog recipe is
        described by what the catalog stores of it (catalog_query); any other by its title's terms (build_item), with
        the tags it comes with. Only the training recipes the catalog holds can be neighbours. EmptyQuery for a title
        with no food description in it."""
        try:
            stored = catalog.food(int(recipe.id)) if is_food_id(recipe.id) else None
            if stored is not None:
                item = catalog_query(stored, catalog.tags(recipe.id), self.clusterer.config.get("UNPRICED_BAND", 3.0))
            else:
                with self.lock:
                    item = build_item(recipe.title, self.extractor, lookup=self.lookup)
                item["tags"] = list(recipe.tags)
            catalog_ids = self.catalog_ids()
            in_catalog = np.isin(catalog_ids, np.fromiter(catalog.fdc_ids(), dtype=np.int64))
            # one more, in case the recipe itself is among them
            result = self.clusterer.predict(item, k=top_k + 1, allowed=in_catalog)
        except EmptyQuery:
            raise
        except Exception:
            logger.exception("Clustering recipe %r failed.", recipe.title)
            return []
        own = int(recipe.id) if stored is not None else None
        nearest = [fdc_id for neighbour in result["neighbours"]
                   if (fdc_id := int(neighbour["id"]) + (FOODCOM_ID_OFFSET if self.clusterer.ids_are_foodcom else 0))
                   != own][:top_k]
        found = catalog.foods_by_ids(nearest)
        logger.debug("Recipe %r: cluster %d (%s); %d neighbours of %d training recipes in the catalog.", recipe.title,
                     result["cluster"], result["cluster_name"], len(found), int(in_catalog.sum()))
        return found
