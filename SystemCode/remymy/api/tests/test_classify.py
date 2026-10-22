from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from remymy_api.dependencies import get_classifier
from remymy_api.inference.food_query import NUTRIENT_COLS, EmptyQuery, UnknownFood
from remymy_api.main import app

WAFFLES = {
    "fdc_id": 167516, "description": "Waffles, buttermilk, frozen, ready-to-heat",
    "product": ["waffles"], "adj": ["ready"], "verb": ["frozen"],
    "nutrients": {column: None for column in NUTRIENT_COLS} | {"energy_kcal": 273.0},
    "training": {"cluster": 60, "labels": ["kid_friendly", "breakfast", "easy", "ready_to_eat"]},
}


def prediction(item: dict) -> dict:
    """RecipeClassifier.predict's result shape for `item`."""
    return {"item": item, "terms": ["waffles", "ready", "frozen"], "known_terms": 3, "model": "fake",
            "labels": ["breakfast", "easy"], "score_kind": "probability",
            "scores": {"breakfast": 0.98, "easy": 0.91, "generic": 0.01}}


class FakeClassifier:
    """Stands in for inference.classification_service.FoodClassifier, recording each call."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def classify(self, query, nutrients=None, terms=None) -> dict:
        self.calls.append(("classify", query, nutrients, terms))
        if not query.strip(" ,"):
            raise EmptyQuery("no food description left")
        item = {key: value for key, value in WAFFLES.items() if key not in ("fdc_id", "training")}
        return prediction(item | {"description": query})

    def classify_fdc(self, fdc_id, nutrients=None) -> dict:
        self.calls.append(("classify_fdc", fdc_id, nutrients))
        if fdc_id != WAFFLES["fdc_id"]:
            raise UnknownFood(f"fdc_id {fdc_id} is not a training food")
        return prediction(WAFFLES)


@pytest.fixture
def client(monkeypatch, tmp_path, cache):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def classifier() -> FakeClassifier:
    fake = FakeClassifier()
    app.dependency_overrides[get_classifier] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_classifier, None)


def test_classify_returns_labels_scores_and_the_terms_used(classifier, client):
    response = client.post("/api/classify", json={"query": "Waffles, buttermilk", "nutrients": {"energy_kcal": 273}})

    assert response.status_code == 200
    body = response.json()
    assert body["labels"] == ["breakfast", "easy"]
    assert body["scores"]["breakfast"] == 0.98
    assert body["fdc_id"] is None and body["training_labels"] is None
    assert classifier.calls == [("classify", "Waffles, buttermilk", {"energy_kcal": 273.0},
                                 {"product": None, "adj": None, "verb": None})]


def test_classify_passes_given_terms_through(classifier, client):
    client.post("/api/classify", json={"query": "waffles", "product": ["waffles"], "verb": ["frozen"]})

    assert classifier.calls[0][3] == {"product": ["waffles"], "adj": None, "verb": ["frozen"]}


def test_classify_by_fdc_id_includes_the_training_labels(classifier, client):
    response = client.get("/api/classify/167516")

    assert response.status_code == 200
    body = response.json()
    assert body["fdc_id"] == 167516
    assert body["training_labels"] == ["kid_friendly", "breakfast", "easy", "ready_to_eat"]


def test_unknown_fdc_id_is_404(classifier, client):
    response = client.get("/api/classify/1")

    assert response.status_code == 404
    assert response.json()["detail"] == "fdc_id 1 is not a training food"


def test_query_with_only_amounts_is_422(classifier, client):
    assert client.post("/api/classify", json={"query": ", ,"}).status_code == 422


@pytest.mark.parametrize("body", [
    {"query": ""},
    {"query": "waffles", "nutrients": {"vitamin_c_mg": 2}},
    {"query": "waffles", "label": "breakfast"},
])
def test_invalid_requests_are_rejected_before_the_model(classifier, client, body):
    assert client.post("/api/classify", json=body).status_code == 422
    assert classifier.calls == []


def test_without_a_classifier_both_routes_are_503(client):
    assert client.post("/api/classify", json={"query": "waffles"}).status_code == 503
    response = client.get("/api/classify/167516")
    assert response.status_code == 503
    assert response.json()["detail"] == "Classification is unavailable: the classifier is not loaded."


def test_real_classifier_reproduces_a_training_food():
    """The saved classifier on a looked-up training food (no GLiNER / stanza needed), if its packages and weights
    are installed."""
    for package in ("sklearn", "umap"):
        pytest.importorskip(package)
    from remymy_api.inference.classification_service import FoodClassifier, classifier_dir, lookup_path
    from remymy_api.inference.food_query import TermExtractor
    from remymy_api.inference.model_inference_recipe_classification import RecipeClassifier

    if not classifier_dir().is_dir() or not lookup_path().exists():
        pytest.skip("no classifier weights or fdc_id lookup")
    # its own extractor, unloaded: a looked-up food never reaches it
    classifier = FoodClassifier(RecipeClassifier(classifier_dir()), TermExtractor(), lookup_path())
    result = classifier.classify_fdc(167516)

    assert sorted(result["labels"]) == sorted(result["item"]["training"]["labels"])
