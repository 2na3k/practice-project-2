"""The inference service the recommend route calls, over the catalog RecipeRepository serves.

A user's History holds whether they liked each recipe they cooked, and RecipeRepository.digest matches each recipe to
catalog foods; those foods, each with its like as a rating and its date, are the user's history for the retrieval
pipeline, which retrieves, reranks and diversifies the catalog's foods for them."""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from statistics import median

from sqlalchemy.orm import Session

from ..db import database_engine
from ..history import History
from ..recipe import Recipe
from ..repository.recipe_repository import RecipeRepository
from ..schemas import CatalogFood
from .model_inference_score_recommend import (CATALOG_NUTRIENTS, DEFAULT_ARTIFACTS, DEFAULT_INDEX_DIR, DIET_COLUMNS,
                                              TEXT_COLUMNS, RetrievalPipeline)
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
                        "entities": {field: [[phrase] for phrase in getattr(food, field)] for field in TEXT_COLUMNS},
                        # the food's diet columns, when the catalog has them (None screens the food by its words)
                        "special_diet": {column: getattr(food, column, None) for column in DIET_COLUMNS.values()}})
    return recipes


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
        pipeline = RetrievalPipeline.from_recipes(catalog_recipes(foods), artifact_dir(), index_dir())
        # at startup, so the first recommendation does not wait for the catalog's index to be built
        keys = repository.load_ingredient_index()
        logger.info("Recommender loaded: %d catalog foods, %d ingredient keys.", len(foods), keys)
        return pipeline

    def recommend(self, history: History, recipes: Mapping[str, Recipe], top_k: int,
                  candidates: int = 100, preferences: Mapping | None = None) -> list[dict]:
        """The pipeline's recommendations from the history's foods; the preferences' diet and budget constrain which
        foods its retrieval may return."""
        # Preparing the history
        history_foods = [
            {"fdc_id": ingredient.fdc_id, "rating": LIKED_RATING if entry.like else DISLIKED_RATING,
             "date": entry.timestamp.date().isoformat()}
            for entry in history
            for ingredient in recipes[entry.recipe_id].ingredients
        ]
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

class Clusterer(Singleton):
    """The recipe clusterer, one per process: every Clusterer() is the same instance. The first construction, at
    startup, loads and warms up every model a query reads, so a query only infers; later ones ignore their arguments."""
    # the model's neighbours are training foods, and not every one is in the priced catalog, so it is asked for more
    CANDIDATES_PER_ITEM = 4

    def _load(self, 
              lookup: Path | None = None) -> None:
        """Loads what is not given: the clustering bundle and the encoder for a term it has not seen, and GLiNER and
        stanza for a query's terms, downloading any not cached yet; then warms the loaded models up."""
        started = time.perf_counter()
        recipe_clusterer = RecipeClusterer(clustering_dir(), lookup_path())
        logger.info("Recipe clusterer loaded: %d clusters over %d training foods.",
                    len(recipe_clusterer.bundle["cluster_names"]), len(recipe_clusterer.bundle["train_labels"]))
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

    def cluster(self, title: str, top_k: int, catalog: RecipeRepository) -> list[CatalogFood]:
        """The top_k catalog foods nearest a recipe, by its title, in its cluster. EmptyQuery for a title with no food
        description in it."""
        try:
            with self.lock:
                item = build_item(title, self.extractor, lookup=self.lookup)
            result = self.clusterer.predict(item, k=top_k * self.CANDIDATES_PER_ITEM)
        except EmptyQuery:
            raise
        except Exception:
            logger.exception("Clustering recipe %r failed.", title)
            return []
        nearest = [neighbour["fdc_id"] for neighbour in result["neighbours"]]
        # a neighbour the catalog does not price is left out
        found = catalog.foods_by_ids(nearest)[:top_k]
        logger.debug("Recipe %r: cluster %d (%s); %d of %d neighbours in the catalog.", title,
                     result["cluster"], result["cluster_name"], len(found), len(nearest))
        return found
