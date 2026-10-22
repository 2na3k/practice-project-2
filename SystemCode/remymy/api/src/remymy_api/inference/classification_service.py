"""The recipe classifier the classify routes call: RecipeClassifier (model_inference_recipe_classification) behind the
query parsing of food_query.

A free-text query goes through GLiNER and stanza, and a term the classifier has not seen through its encoder; startup
loads them all, so a query only infers. A food looked up by fdc_id in the premade lookup needs none of them."""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from .food_query import DEFAULT_LOOKUP, TermExtractor, build_item, lookup_item
from .model_inference_recipe_classification import DEFAULT_ARTIFACTS, RecipeClassifier
from .singleton import Singleton

logger = logging.getLogger(__name__)


def classifier_dir() -> Path:
    return Path(os.getenv("CLASSIFIER_ARTIFACT_DIR", str(DEFAULT_ARTIFACTS)))


def lookup_path() -> Path:
    return Path(os.getenv("FDC_LOOKUP_PATH", str(DEFAULT_LOOKUP)))


class FoodClassifier(Singleton):
    """The classifier, the term extractor and the fdc_id lookup it reads, one per process: every FoodClassifier() is
    the same instance. Startup constructs it; a route's FoodClassifier() is then that loaded instance."""

    def _load(self, classifier: RecipeClassifier | None = None, extractor: TermExtractor | None = None,
              lookup: Path | None = None) -> None:
        """Loads what is not given: the classifier and its encoder of unseen terms, and GLiNER and stanza for a
        query's terms, downloading any not cached yet; then warms the loaded models up."""
        started = time.perf_counter()
        warm = classifier is None
        if classifier is None:
            classifier = RecipeClassifier(classifier_dir())
            logger.info("Recipe classifier loaded: %s, %d labels.", classifier.kind, len(classifier.label_cols))
            classifier.load_term_model()
        self.classifier = classifier
        self.extractor = extractor or TermExtractor().load()
        self.lookup = lookup or lookup_path()
        # One query at a time: neither the extractor's models nor the encoder are meant to be shared across threads.
        self.lock = threading.Lock()
        if warm:
            logger.info("Classifier models loaded in %.1fs.", time.perf_counter() - started)
            self.warm_up()

    def warm_up(self) -> None:
        """One query through the whole classifier, so the first one does not wait for what only a first call does,
        numba compiling the nutrients' UMAP transform."""
        started = time.perf_counter()
        # an invented product term, so the encoder of unseen terms runs too
        self.classify("Tomato soup, canned", terms={"product": ["remymy warm up"]})
        logger.info("Classifier warmed up in %.1fs.", time.perf_counter() - started)

    @property
    def model(self) -> str:
        return self.classifier.kind

    def classify(self, query: str, nutrients: Mapping[str, float] | None = None,
                 terms: Mapping[str, Sequence[str] | None] | None = None) -> dict:
        """The prediction for a free-text query, or for a training food when the query is an fdc_id. `nutrients` and
        `terms` override what the query holds. EmptyQuery for a query with no description, UnknownFood for an fdc_id
        that is not in the lookup."""
        with self.lock:
            item = build_item(query, self.extractor, dict(nutrients or {}), dict(terms or {}), lookup=self.lookup)
            return self.classifier.predict(item)

    def classify_fdc(self, fdc_id: int, nutrients: Mapping[str, float] | None = None) -> dict:
        """The prediction for a training food from the lookup, with its training labels in the item."""
        with self.lock:
            return self.classifier.predict(lookup_item(fdc_id, dict(nutrients or {}), lookup=self.lookup))
