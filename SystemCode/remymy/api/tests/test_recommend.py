from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from remymy_api import main, recommend
from remymy_api.main import app, load_recommender
from remymy_api.recipe import Recipe
from remymy_api.schemas import Ingredient
from remymy_api.users import add_history, create_user, init_user_database, open_user_database


CATALOG = {
    "tomatoes": {"fdc_id": 1, "name": "Tomatoes, red, ripe", "price": 2.0,
                 "nutrient_values": {"energy_kcal": 18.0, "protein_g": 0.9}},
    "salt": {"fdc_id": 2, "name": "Salt, table", "price": 1.0,
             "nutrient_values": {"energy_kcal": 0.0, "sodium_mg": 38758.0}},
    "apple": {"fdc_id": 3, "name": "Apples, raw", "price": 3.0,
              "nutrient_values": {"energy_kcal": 52.0}},
}
RECOMMENDED = [
    {"fdc_id": 167516, "name": "Waffles, buttermilk, frozen", "price": 6.85, "p_like": 0.8, "similarity": 0.9},
    {"fdc_id": 175237, "name": "Beans, black, cooked", "price": 2.72, "p_like": 0.7, "similarity": 0.9},
    {"fdc_id": 168516, "name": "Cabbage, savoy, cooked", "price": 4.2, "p_like": 0.6, "similarity": 0.9},
]


class FakeRepository:
    """Stands in for the shared recipe_repository: an ingredient line matches the CATALOG food it names exactly."""

    def __init__(self) -> None:
        self.digested: list[str] = []

    def digest(self, recipe_id: str, lines: list[str]) -> Recipe:
        self.digested.append(recipe_id)
        return Recipe(recipe_id, [Ingredient(**CATALOG[line]) for line in dict.fromkeys(lines) if line in CATALOG])


def answer(history, top_k: int) -> list[dict]:
    return RECOMMENDED if any(entry.recipe.ingredients for entry in history) else []


@pytest.fixture
def stores(tmp_path, monkeypatch):
    recipes = tmp_path / "recipes.db"
    with sqlite3.connect(recipes) as connection:
        connection.executescript(
            """
            CREATE TABLE recipe (recipe_id TEXT PRIMARY KEY, title TEXT, quality_status TEXT NOT NULL);
            CREATE TABLE recipe_ingredient (
              ingredient_id TEXT PRIMARY KEY, recipe_id TEXT NOT NULL, position INTEGER NOT NULL,
              raw_text TEXT NOT NULL, ingredient_text TEXT
            );
            CREATE TABLE recipe_instruction (recipe_id TEXT, position INTEGER, text TEXT);
            INSERT INTO recipe VALUES
              ('openrecipes:1', 'Tomato Soup', 'accepted'),
              ('openrecipes:2', 'Apple Salad', 'accepted'),
              ('openrecipes:3', 'Plain Water', 'accepted');
            INSERT INTO recipe_ingredient VALUES
              ('1:1', 'openrecipes:1', 1, '2 cups tomatoes', 'tomatoes'),
              ('1:2', 'openrecipes:1', 2, '1 tsp salt', 'salt'),
              ('1:3', 'openrecipes:1', 3, 'salt to taste', 'salt'),
              ('1:4', 'openrecipes:1', 4, '4 cups water', 'water'),
              ('2:1', 'openrecipes:2', 1, 'apple', NULL),
              ('3:1', 'openrecipes:3', 1, '1 cup water', 'water');
            """
        )
    connection.close()
    monkeypatch.setenv("DATABASE_PATH", str(recipes))
    monkeypatch.setenv("USER_DATABASE_PATH", str(tmp_path / "state" / "users.db"))
    init_user_database()
    connection = open_user_database()
    yield connection
    connection.close()


@pytest.fixture
def repository(monkeypatch) -> FakeRepository:
    fake = FakeRepository()
    monkeypatch.setattr(recommend, "recipe_repository", fake)
    return fake


@pytest.fixture
def models(recommender, repository):
    recommender.handler = answer
    return recommender


def seed_alice(stores, cache) -> int:
    alice = create_user(stores, cache, "alice")
    add_history(stores, alice.id, "openrecipes:1", rating=4)
    add_history(stores, alice.id, "openrecipes:2")
    return alice.id


def test_first_request_calls_the_model_once_and_caches_by_user_id(stores, models, cache):
    alice = seed_alice(stores, cache)

    with TestClient(app) as client:
        response = client.get("/api/recommend/alice", params={"top_k": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["profile"] == {
        "user_id": alice,
        "avg_price": pytest.approx(2.0),
        "avg_nutrient_values": {
            "energy_kcal": pytest.approx(70 / 3),
            "protein_g": pytest.approx(0.9),
            "sodium_mg": pytest.approx(38758.0),
        },
    }
    assert [item["id"] for item in body["items"]] == ["167516", "175237"]

    (call,) = models.calls
    assert call["top_k"] == recommend.MAX_TOP_K
    history = call["history"]
    assert history.user_id == alice
    # oldest first, each recipe's lines matched once ("salt" twice, "water" to nothing)
    assert [(entry.recipe.recipe_id, entry.rating) for entry in history] == [
        ("openrecipes:1", 4), ("openrecipes:2", None),
    ]
    assert [[ingredient.fdc_id for ingredient in entry.recipe.ingredients] for entry in history] == [[1, 2], [3]]
    today = datetime.now(timezone.utc).date()
    assert all(entry.timestamp.date() == today for entry in history)

    cached = json.loads(cache.store[f"recommendations:{alice}"])
    assert len(cached["items"]) == 3
    assert cache.ttls[f"recommendations:{alice}"] == 86400


def test_a_recipe_rated_twice_is_digested_once_and_both_ratings_refer_to_it(stores, models, repository, cache):
    alice = create_user(stores, cache, "alice")
    add_history(stores, alice.id, "openrecipes:1", rating=2)
    add_history(stores, alice.id, "openrecipes:1", rating=5)

    with TestClient(app) as client:
        response = client.get("/api/recommend/alice")

    assert response.status_code == 200
    first, again = models.calls[0]["history"]
    assert (first.rating, again.rating) == (2, 5)
    assert first.recipe is again.recipe
    assert repository.digested == ["openrecipes:1"]
    # each rating counts the recipe's foods: tomatoes 2.0 and salt 1.0, twice
    assert response.json()["profile"]["avg_price"] == pytest.approx(1.5)


def test_later_requests_are_served_from_the_cache(stores, models, cache):
    seed_alice(stores, cache)

    with TestClient(app) as client:
        first = client.get("/api/recommend/alice", params={"top_k": 1}).json()
        second = client.get("/api/recommend/alice", params={"top_k": 3}).json()

    assert len(models.calls) == 1
    assert [item["id"] for item in first["items"]] == ["167516"]
    assert [item["id"] for item in second["items"]] == ["167516", "175237", "168516"]
    assert second["profile"] == first["profile"]


def test_user_lookup_goes_to_redis_before_the_database(stores, models, cache):
    alice = seed_alice(stores, cache)
    cache.store.clear()

    with TestClient(app) as client:
        client.get("/api/recommend/alice")
        client.get("/api/recommend/alice")

    assert cache.reads[0] == "user:alice"
    assert cache.store["user:alice"] == str(alice).encode()
    assert cache.ttls["user:alice"] == 86400
    assert len(models.calls) == 1


def test_unknown_user_neither_calls_the_model_nor_caches(stores, models, cache):
    with TestClient(app) as client:
        response = client.get("/api/recommend/nobody")

    assert response.json() == {"profile": None, "items": []}
    assert models.calls == []
    assert cache.store == {}


def test_history_with_no_priced_ingredient_has_no_profile(stores, models, cache):
    bob = create_user(stores, cache, "bob")
    add_history(stores, bob.id, "openrecipes:3", rating=5)

    with TestClient(app) as client:
        response = client.get("/api/recommend/bob")

    assert response.json() == {"profile": None, "items": []}
    assert len(models.calls) == 1
    assert f"recommendations:{bob.id}" in cache.store


def test_redis_down_still_answers_from_the_model(stores, models, cache):
    seed_alice(stores, cache)
    cache.down = True

    with TestClient(app) as client:
        first = client.get("/api/recommend/alice")
        second = client.get("/api/recommend/alice")

    assert first.status_code == second.status_code == 200
    assert len(first.json()["items"]) == 3
    assert len(models.calls) == 2


def test_a_computed_recommendation_is_logged_and_a_cache_hit_at_debug(stores, models, cache, caplog):
    alice = seed_alice(stores, cache)

    with caplog.at_level(logging.DEBUG, logger="remymy_api"):
        with TestClient(app) as client:
            client.get("/api/recommend/alice")
            client.get("/api/recommend/alice")

    routes = [record for record in caplog.records if record.name == "remymy_api.routes"]
    computed = [record for record in routes if record.levelno == logging.INFO]
    assert len(computed) == 1
    assert computed[0].getMessage().startswith(f"Recommended 3 foods for user {alice} from 2 history recipes in ")
    assert [record.getMessage() for record in routes if record.levelno == logging.DEBUG] == [
        f"Recommendations for user {alice} served from the cache."
    ]


def test_redis_failures_are_logged_as_warnings(stores, models, cache, caplog):
    seed_alice(stores, cache)
    cache.down = True

    with TestClient(app) as client:
        client.get("/api/recommend/alice")

    warnings = [record.getMessage() for record in caplog.records
                if record.name == "remymy_api.cache" and record.levelno == logging.WARNING]
    assert "Redis read of user:alice failed: down" in warnings
    assert any(message.startswith("Redis write of recommendations:") for message in warnings)


def test_without_a_catalog_the_api_starts_and_recommendations_are_503(stores, cache, monkeypatch):
    # what recipe_repository is when neither a catalog database nor a CSV was there on import
    monkeypatch.setattr(main, "recipe_repository", None)
    monkeypatch.setattr(main, "load_recommender", load_recommender)
    alice = seed_alice(stores, cache)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/api/recommend/alice")

    assert response.status_code == 503
    assert f"recommendations:{alice}" not in cache.store


def test_the_recommender_is_built_on_the_shared_repository(monkeypatch):
    from remymy_api.inference import service

    shared = object()
    monkeypatch.setattr(main, "recipe_repository", shared)
    monkeypatch.setattr(service.Recommender, "load", classmethod(lambda cls, repository: repository))
    assert load_recommender() is shared
