"""The inference service the recommend route calls, over the catalog RecipeRepository serves.

A user's History holds whether they liked each recipe they cooked, and RecipeRepository.digest matches each recipe to
catalog foods; those foods, each with its like as a rating and its date, are the user's history for the retrieval
pipeline, which retrieves, reranks and diversifies the catalog's foods for them."""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from statistics import median

from ..history import History
from ..recipe import Recipe
from ..repository.recipe_repository import RecipeRepository
from ..schemas import CatalogFood
from .model_inference_reranker_avgemb import DEFAULT_ARTIFACTS
from .model_inference_score_recommend import CATALOG_NUTRIENTS, DEFAULT_INDEX_DIR, TEXT_COLUMNS, RetrievalPipeline

logger = logging.getLogger(__name__)

# The model reads Food.com's 0-5 ratings, of which training counted 3 to 5 as liked: a like goes in as the top
# rating, a dislike as the bottom one.
LIKED_RATING, DISLIKED_RATING = 5, 0


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


class Recommender:
    """The retrieval pipeline, built from the repository's catalog."""

    def __init__(self, repository: RecipeRepository, pipeline: RetrievalPipeline) -> None:
        self.repository = repository
        self.pipeline = pipeline
        # One recommendation at a time, as the model service's single queue worker ran them: the pipeline keeps
        # per-user state between calls.
        self.lock = threading.Lock()

    @classmethod
    def load(cls, repository: RecipeRepository) -> Recommender:
        foods = repository.all_foods()
        pipeline = RetrievalPipeline.from_recipes(catalog_recipes(foods), artifact_dir(), index_dir())
        # at startup, so the first recommendation does not wait for the repository to build it
        keys = repository.load_ingredient_index()
        logger.info("Recommender loaded: %d catalog foods, %d ingredient keys.", len(foods), keys)
        return cls(repository, pipeline)

    def recommend(self, history: History, recipes: Mapping[str, Recipe], top_k: int,
                  candidates: int = 100) -> list[dict]:
        """The catalog foods to recommend to the user whose history this is, best first. recipes holds the digested
        Recipe of each recipe_id in history. Each food a recipe matched goes to the pipeline with that entry's like,
        as LIKED_RATING or DISLIKED_RATING, and its date."""
        foods = [
            {"fdc_id": ingredient.fdc_id, "rating": LIKED_RATING if entry.like else DISLIKED_RATING,
             "date": entry.timestamp.date().isoformat()}
            for entry in history
            for ingredient in recipes[entry.recipe_id].ingredients
        ]
        results = []
        if foods:
            # The pipeline reports each of its steps through log; at DEBUG they go to this module's logger.
            trace = logger.debug if logger.isEnabledFor(logging.DEBUG) else None
            with self.lock:
                results = self.pipeline.recommend({"history": foods}, n_candidates=candidates, top_k=top_k, log=trace)
        logger.debug("%d history recipes matched %d catalog foods; %d recommended.", len(history), len(foods),
                     len(results))
        return [
            {key: result[key] for key in ("fdc_id", "name", "price", "p_like", "similarity")}
            for result in results
        ]
