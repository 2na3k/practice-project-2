from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

import numpy as np

from remymy_api.dependencies import get_clusterer, get_recipe_repository
from remymy_api.inference.food_query import EmptyQuery
from remymy_api.main import app
from remymy_api.repository.recipe_repository import FOODCOM_ID_OFFSET
from remymy_api.schemas import RecipeDetail

from test_recipe_repository import catalog_food

ITEM = {"id": "openrecipes:5210", "title": "Roast Chicken with Carrots", "ingredients": ["1 chicken", "4 carrots"],
        "instructions": ["Roast."], "quality_status": "ok"}


class FakeClusterer:
    """Stands in for inference.service.Clusterer, recording each call: the model's neighbours are 170000 on, and the
    catalog keeps the ones it prices."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def cluster(self, recipe, top_k, catalog) -> list:
        self.calls.append((recipe.title, top_k))
        if not recipe.title.strip(" ,"):
            raise EmptyQuery("no food description left")
        return catalog.foods_by_ids(range(170000, 170000 + top_k * 4))[:top_k]


class FakeCatalog:
    """Prices only the even fdc_ids."""

    def foods_by_ids(self, fdc_ids):
        return [catalog_food(fdc_id, f"food {fdc_id}") for fdc_id in fdc_ids if fdc_id % 2 == 0]


@pytest.fixture
def client(monkeypatch, tmp_path, cache):
    monkeypatch.setenv("USER_DATABASE_PATH", str(tmp_path / "state" / "users.db"))
    app.dependency_overrides[get_recipe_repository] = lambda: FakeCatalog()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_recipe_repository, None)


@pytest.fixture
def clusterer() -> FakeClusterer:
    fake = FakeClusterer()
    app.dependency_overrides[get_clusterer] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_clusterer, None)


def test_cluster_returns_the_k_nearest_catalog_foods_of_the_title(clusterer, client):
    response = client.post("/api/cluster?k=3", json={"item": ITEM})

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["near_items"]
    # the odd fdc_ids are not in the catalog, so they are left out
    assert [item["fdc_id"] for item in body["near_items"]] == [170000, 170002, 170004]
    assert body["near_items"][0]["description"] == "food 170000"
    assert clusterer.calls == [("Roast Chicken with Carrots", 3)]


def test_without_a_catalog_it_is_503(clusterer, client):
    app.dependency_overrides[get_recipe_repository] = lambda: None

    response = client.post("/api/cluster", json={"item": ITEM})

    assert response.status_code == 503
    assert clusterer.calls == []


def test_title_with_only_amounts_is_422(clusterer, client):
    assert client.post("/api/cluster", json={"item": ITEM | {"title": ", ,"}}).status_code == 422


@pytest.mark.parametrize(("query", "body"), [
    ("", {}),
    ("", {"item": {"title": "Roast chicken"}}),
    ("", {"item": ITEM, "k": 3}),
    ("?k=0", {"item": ITEM}),
    ("?k=51", {"item": ITEM}),
])
def test_invalid_requests_are_rejected_before_the_model(clusterer, client, query, body):
    assert client.post(f"/api/cluster{query}", json=body).status_code == 422
    assert clusterer.calls == []


def test_without_a_clusterer_it_is_503(client):
    response = client.post("/api/cluster", json={"item": ITEM})
    assert response.status_code == 503
    assert response.json()["detail"] == "Clustering is unavailable: the clusterer is not loaded."


class FakeRecipeClusterer:
    """Stands in for RecipeClusterer: its training ids nearest first, all of cluster 12; a neighbour is one the caller
    allows."""

    def __init__(self, train_ids, foodcom=False) -> None:
        self.train_ids = np.array(train_ids)
        self.ids_are_foodcom = foodcom
        self.config = {"UNPRICED_BAND": 3.0}
        self.asked: list[tuple] = []

    def predict(self, item, k, allowed=None):
        self.asked.append((item, k))
        rows = [row for row in range(len(self.train_ids)) if allowed is None or allowed[row]][:k]
        return {"item": item, "cluster": 12, "cluster_name": "chicken, broiler fryer",
                "neighbours": [{"id": int(self.train_ids[row])} for row in rows]}


class StoredCatalog(FakeCatalog):
    """Holds the even fdc_ids and the Food.com recipes 137739 and 31490, the first one tagged."""

    def __init__(self) -> None:
        self.ids = {*range(170000, 170010, 2), FOODCOM_ID_OFFSET + 137739, FOODCOM_ID_OFFSET + 31490}

    def fdc_ids(self):
        return self.ids

    def foods_by_ids(self, fdc_ids):
        return [catalog_food(fdc_id, f"food {fdc_id}") for fdc_id in fdc_ids if fdc_id in self.ids]

    def food(self, fdc_id):
        return catalog_food(fdc_id, f"food {fdc_id}", unpriced_items=["salt"]) if fdc_id in self.ids else None

    def tags(self, recipe_id):
        return ["60_minutes_or_less"] if recipe_id == str(FOODCOM_ID_OFFSET + 137739) else []


def recipe(title, recipe_id="openrecipes:5210", **values):
    return RecipeDetail(id=recipe_id, title=title, ingredients=["1 chicken"], instructions=[], quality_status="ok",
                        **values)


def clusterer_on(recipe_clusterer, extractor):
    """A Clusterer on fakes. Made without Clusterer(), whose _load loads the real models; and not the singleton, so no
    other test gets it."""
    from remymy_api.inference.service import Clusterer

    clusterer = object.__new__(Clusterer)
    clusterer.clusterer = recipe_clusterer
    clusterer.extractor = extractor
    clusterer.lookup = None
    clusterer.lock = threading.Lock()
    return clusterer


def test_clusterer_finds_the_nearest_among_the_training_recipes_the_catalog_holds():
    recipe_clusterer = FakeRecipeClusterer(range(170000, 170010))
    clusterer = clusterer_on(recipe_clusterer, lambda description: {"product": ["chicken"], "adj": [], "verb": []})

    found = clusterer.cluster(recipe("Roast Chicken with Carrots", tags=["weeknight"]), 2, StoredCatalog())

    # the odd fdc_ids are not in the catalog, so they are never neighbours
    assert [food.fdc_id for food in found] == [170000, 170002]
    item, k = recipe_clusterer.asked[0]
    # a recipe the catalog does not hold is its title's terms, with the tags it comes with
    assert (item["product"], item["tags"], k) == (["chicken"], ["weeknight"], 3)


def test_a_catalog_recipe_is_its_stored_row_and_not_its_own_neighbour():
    recipe_clusterer = FakeRecipeClusterer([137739, 5, 31490], foodcom=True)
    clusterer = clusterer_on(recipe_clusterer, lambda description: pytest.fail("the title is not read"))

    chowder = str(FOODCOM_ID_OFFSET + 137739)
    found = clusterer.cluster(recipe("Smoked Salmon Chowder", chowder), 2, StoredCatalog())

    # 5 is not in the catalog, and the chowder itself is left out
    assert [food.fdc_id for food in found] == [FOODCOM_ID_OFFSET + 31490]
    item, _ = recipe_clusterer.asked[0]
    assert (item["product"], item["adj"], item["tags"]) == (["apple"], ["fresh"], ["60_minutes_or_less"])
    # 2.5 priced, give or take 3 for its one unpriced ingredient
    assert item["price_interval"] == [0.0, 5.5]
    assert item["nutrients"]["energy_kcal"] == 10.0


def test_clusterer_lets_an_empty_title_through_as_empty_query():
    clusterer = clusterer_on(FakeRecipeClusterer([170000]), lambda description: {})

    with pytest.raises(EmptyQuery):
        clusterer.cluster(recipe(", ,"), 2, StoredCatalog())
