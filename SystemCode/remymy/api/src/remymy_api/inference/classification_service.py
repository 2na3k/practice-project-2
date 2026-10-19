"""The recipe classifier the classify routes call: RecipeClassifier (model_inference_recipe_classification) behind the
query parsing of food_query.

A free-text query goes through GLiNER and stanza, which load on the first such query; a food looked up by fdc_id in the
premade lookup needs neither."""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path

from .food_query import DEFAULT_LOOKUP, TermExtractor, build_item, lookup_item
from .model_inference_recipe_classification import DEFAULT_ARTIFACTS, RecipeClassifier

logger = logging.getLogger(__name__)


def classifier_dir() -> Path:
    return Path(os.getenv("CLASSIFIER_ARTIFACT_DIR", str(DEFAULT_ARTIFACTS)))


def lookup_path() -> Path:
    return Path(os.getenv("FDC_LOOKUP_PATH", str(DEFAULT_LOOKUP)))


class FoodClassifier:
    """The classifier, the term extractor and the fdc_id lookup it reads."""

    def __init__(self, classifier: RecipeClassifier, extractor: TermExtractor, lookup: Path) -> None:
        self.classifier = classifier
        self.extractor = extractor
        self.lookup = lookup
        # One query at a time: the extractor loads GLiNER and stanza on first use, and neither they nor the
        # encoder are meant to be shared across threads.
        self.lock = threading.Lock()

    @classmethod
    def load(cls) -> FoodClassifier:
        classifier = RecipeClassifier(classifier_dir())
        logger.info("Recipe classifier loaded: %s, %d labels.", classifier.kind, len(classifier.label_cols))
        return cls(classifier, TermExtractor(), lookup_path())

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
