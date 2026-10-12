"""The inference service the recommend route calls, over the catalog RecipeRepository serves.

A history recipe's ingredient lines are matched to catalog foods by name key; those foods, each with its recipe's
rating and date, are the user's history for the retrieval pipeline, which retrieves, reranks and diversifies the
catalog's foods for them."""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from statistics import median

from ..repository.recipe_repository import RecipeRepository
from ..schemas import CatalogFood
from .model_inference_reranker_avgemb import DEFAULT_ARTIFACTS, normalize_price_text
from .model_inference_score_recommend import CATALOG_NUTRIENTS, DEFAULT_INDEX_DIR, TEXT_COLUMNS, RetrievalPipeline

logger = logging.getLogger(__name__)

# The nutrients a matched ingredient reports, for the buying profile.
NUTRIENT_COLUMNS = (
    "energy_kcal", "protein_g", "fat_g", "satfat_g", "carb_g", "sugars_g", "fiber_g", "sodium_mg", "cholesterol_mg",
)


def artifact_dir() -> Path:
    return Path(os.getenv("ARTIFACT_DIR", str(DEFAULT_ARTIFACTS)))


def index_dir() -> Path:
    return Path(os.getenv("INDEX_DIR", str(DEFAULT_INDEX_DIR)))


def catalog_recipes(foods: list[CatalogFood]) -> list[dict]:
    """The catalog's foods → recipes in the reranker's input format, in the order given.

    product / adj / verb are already NER output, so each phrase becomes one ingredient of "entities" and no tagging
    is needed. total_price is 0 whenever an item is unpriced, so price is the sum of the items' pack prices instead
    (as in recipe_clustering.ipynb). A nutrient FDC has no value for (sugars_g for 13% of foods) takes the catalog
    median."""
    medians = {}
    for column in CATALOG_NUTRIENTS.values():
        known = [value for food in foods if (value := getattr(food, column)) is not None]
        medians[column] = median(known) if known else float("nan")
    recipes = []
    for food in foods:
        nutrients = {}
        for key, column in CATALOG_NUTRIENTS.items():
            value = getattr(food, column)
            nutrients[key] = float(medians[column] if value is None else value)
        recipes.append({"fdc_id": food.fdc_id, "name": food.description,
                        "price": float(sum(item.price for item in food.item_prices.values())),
                        "nutrients": nutrients,
                        "entities": {field: [[phrase] for phrase in getattr(food, field)] for field in TEXT_COLUMNS}})
    return recipes


def ingredient_index(foods: list[CatalogFood]) -> dict[str, dict[str, object]]:
    """One fully priced food per name key, the first comma-separated part of the description normalized ("Waffles,
    buttermilk, frozen" → "waffle"): of a key's foods, the one whose total_price is nearest their median."""
    groups: dict[str, list[CatalogFood]] = {}
    for food in sorted(foods, key=lambda food: food.fdc_id):
        key = normalize_price_text(food.description.split(",", 1)[0].strip())
        if key and food.priced_share == 1 and food.total_price > 0:
            groups.setdefault(key, []).append(food)
    index = {}
    for key, group in groups.items():
        middle = median(food.total_price for food in group)
        # min keeps the first of a tie, the lowest fdc_id
        food = min(group, key=lambda food: abs(food.total_price - middle))
        index[key] = {
            "fdc_id": food.fdc_id,
            "name": food.description,
            "price": food.total_price,
            "nutrient_values": {
                column: value for column in NUTRIENT_COLUMNS if (value := getattr(food, column)) is not None
            },
        }
    return index


def match_entry(index: dict[str, dict[str, object]], texts: list[str]) -> list[dict[str, object]]:
    keys = dict.fromkeys(normalize_price_text(text) for text in texts)
    return [index[key] for key in keys if key in index]


class Recommender:
    """The retrieval pipeline and the ingredient index, both built from the repository's catalog."""

    def __init__(self, repository: RecipeRepository, pipeline: RetrievalPipeline,
                 ingredients: dict[str, dict[str, object]]) -> None:
        self.repository = repository
        self.pipeline = pipeline
        self.ingredients = ingredients
        # One recommendation at a time, as the model service's single queue worker ran them: the pipeline keeps
        # per-user state between calls.
        self.lock = threading.Lock()

    @classmethod
    def load(cls, repository: RecipeRepository) -> Recommender:
        foods = repository.all_foods()
        pipeline = RetrievalPipeline.from_recipes(catalog_recipes(foods), artifact_dir(), index_dir())
        ingredients = ingredient_index(foods)
        logger.info("Recommender loaded: %d catalog foods, %d ingredient keys.", len(foods), len(ingredients))
        return cls(repository, pipeline, ingredients)

    def recommend(self, history: list[dict], top_k: int, candidates: int = 100) -> dict[str, list]:
        """history holds one {"rating", "date", "ingredients"} per rated recipe. Returns the catalog foods each
        recipe's ingredients matched ("ingredients", one list per recipe) and the recommended foods ("items")."""
        matched = [match_entry(self.ingredients, entry["ingredients"]) for entry in history]
        foods = [
            {"fdc_id": food["fdc_id"], "rating": entry["rating"], "date": entry["date"]}
            for entry, ingredients in zip(history, matched)
            for food in ingredients
        ]
        results = []
        if foods:
            # The pipeline reports each of its steps through log; at DEBUG they go to this module's logger.
            trace = logger.debug if logger.isEnabledFor(logging.DEBUG) else None
            with self.lock:
                results = self.pipeline.recommend({"history": foods}, n_candidates=candidates, top_k=top_k, log=trace)
        logger.debug("%d history recipes matched %d catalog foods; %d recommended.", len(history), len(foods),
                     len(results))
        return {
            "ingredients": matched,
            "items": [
                {key: result[key] for key in ("fdc_id", "name", "price", "p_like", "similarity")}
                for result in results
            ],
        }
