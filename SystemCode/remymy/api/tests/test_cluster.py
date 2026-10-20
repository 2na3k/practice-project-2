from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from remymy_api import main
from remymy_api.dependencies import get_recipe_repository
from remymy_api.inference.food_query import EmptyQuery
from remymy_api.main import app

from test_recipe_repository import catalog_food

ITEM = {"id": "openrecipes:5210", "title": "Roast Chicken with Carrots", "ingredients": ["1 chicken", "4 carrots"],
        "instructions": ["Roast."], "quality_status": "ok"}


class FakeClusterer:
    """Stands in for inference.service.Clusterer, recording each call: the model's neighbours are 170000 on, and the
    catalog keeps the ones it prices."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def cluster(self, title, top_k, catalog) -> list:
        self.calls.append((title, top_k))
        if not title.strip(" ,"):
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
def clusterer(monkeypatch) -> FakeClusterer:
    fake = FakeClusterer()
    monkeypatch.setattr(main, "load_clusterer", lambda classifier: fake)
    return fake


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
    """Stands in for RecipeClusterer: neighbours 170000 on, each of cluster 12."""

    def __init__(self) -> None:
        self.asked: list[int] = []

    def predict(self, item, k):
        self.asked.append(k)
        return {"item": item, "cluster": 12, "cluster_name": "chicken, broiler fryer",
                "neighbours": [{"fdc_id": 170000 + i} for i in range(k)]}


def test_clusterer_keeps_the_nearest_catalog_foods():
    from remymy_api.inference.service import Clusterer

    recipe_clusterer = FakeRecipeClusterer()
    clusterer = Clusterer(recipe_clusterer, extractor=lambda description: {"product": ["chicken"], "adj": [], "verb": []},
                          lookup=None)

    found = clusterer.cluster("Roast Chicken with Carrots", 2, FakeCatalog())

    assert [food.fdc_id for food in found] == [170000, 170002]
    assert recipe_clusterer.asked == [8]


def test_clusterer_lets_an_empty_title_through_as_empty_query():
    from remymy_api.inference.service import Clusterer

    clusterer = Clusterer(FakeRecipeClusterer(), extractor=lambda description: {}, lookup=None)

    with pytest.raises(EmptyQuery):
        clusterer.cluster(", ,", 2, FakeCatalog())
