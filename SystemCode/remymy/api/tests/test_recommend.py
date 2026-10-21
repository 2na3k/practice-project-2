from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from sqlalchemy.orm import Session

from remymy_api import main, recommend
from remymy_api.db import users_engine
from remymy_api.dependencies import get_recipe_repository
from remymy_api.main import app, load_recommender
from remymy_api.recipe import Recipe
from remymy_api.repository.user_repository import UserRepository
from remymy_api.schemas import Ingredient
from remymy_api.users import init_user_database


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
    """Stands in for the catalog's RecipeRepository: an ingredient line matches the CATALOG food it names exactly."""

    def __init__(self) -> None:
        self.digested: list[str] = []

    def digest(self, recipe_id: str, lines: list[str]) -> Recipe:
        self.digested.append(recipe_id)
        return Recipe(recipe_id, [Ingredient(**CATALOG[line]) for line in dict.fromkeys(lines) if line in CATALOG])

    def food(self, fdc_id: int) -> dict | None:
        found = next((food for food in CATALOG.values() if food["fdc_id"] == fdc_id), None)
        if found is None:
            # a recommended food, as the groceries panel rates it
            found = next(({"fdc_id": item["fdc_id"], "name": item["name"], "price": item["price"], "nutrient_values": {}}
                          for item in RECOMMENDED if item["fdc_id"] == fdc_id), None)
        return found

    def digest_food(self, food_id: str) -> Recipe:
        self.digested.append(food_id)
        food = self.food(int(food_id))
        return Recipe(food_id, [] if food is None else [Ingredient(**food)])


def answer(history, recipes, top_k: int) -> list[dict]:
    return RECOMMENDED if any(recipes[entry.recipe_id].ingredients for entry in history) else []


@pytest.fixture
def stores(tmp_path, monkeypatch, cache):
    """The recipe database with three recipes, and a UserRepository on a new users database."""
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
    with Session(users_engine()) as session:
        yield UserRepository(session, cache)


@pytest.fixture
def repository(override) -> FakeRepository:
    fake = FakeRepository()
    # the recommend and rating routes get it as the catalog
    override(get_recipe_repository, lambda: fake)
    return fake


@pytest.fixture
def models(recommender, repository):
    recommender.handler = answer
    return recommender


def seed_alice(stores) -> int:
    alice = stores.create_user("alice")
    stores.add_history(alice.id, "openrecipes:1", like=True)
    stores.add_history(alice.id, "openrecipes:2", like=False)
    return alice.id


def test_the_users_preferences_reach_the_recommender(stores, models):
    from remymy_api.schemas import Preferences

    bob = stores.create_user("bob", Preferences(special_diet="halal", preferred_nutrient="protein", budget=8.5))
    stores.add_history(bob.id, "openrecipes:1", like=True)

    with TestClient(app) as client:
        assert client.get("/api/recommend/bob").status_code == 200

    (call,) = models.calls
    assert call["preferences"] == {"special_diet": "halal", "cuisines": [], "preferred_nutrient": "protein",
                                   "budget": 8.5}


def test_first_request_calls_the_model_once_and_caches_by_user_id(stores, models, cache):
    alice = seed_alice(stores)

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
    assert [(entry.recipe_id, entry.like) for entry in history] == [
        ("openrecipes:1", True), ("openrecipes:2", False),
    ]
    recipes = call["recipes"]
    assert [[ingredient.fdc_id for ingredient in recipes[entry.recipe_id].ingredients] for entry in history] == [
        [1, 2], [3],
    ]
    today = datetime.now(timezone.utc).date()
    assert all(entry.timestamp.date() == today for entry in history)

    cached = json.loads(cache.store[f"recommendations:{alice}"])
    assert len(cached["items"]) == 3
    assert cache.ttls[f"recommendations:{alice}"] == 86400


def test_rating_a_recipe_again_changes_its_like(stores, models, repository, cache):
    alice = stores.create_user("alice")
    stores.add_history(alice.id, "openrecipes:1", like=False)
    stores.add_history(alice.id, "openrecipes:1", like=True)

    with TestClient(app) as client:
        response = client.get("/api/recommend/alice")

    assert response.status_code == 200
    (call,) = models.calls
    assert [(entry.recipe_id, entry.like) for entry in call["history"]] == [("openrecipes:1", True)]
    assert repository.digested == ["openrecipes:1"]
    # tomatoes 2.0 and salt 1.0
    assert response.json()["profile"]["avg_price"] == pytest.approx(1.5)


def test_a_rated_grocery_goes_to_the_model_as_itself(stores, models, repository, cache):
    alice = stores.create_user("alice")
    # "Apples, raw" of CATALOG, by its fdc_id, as the groceries panel rates it
    stores.add_history(alice.id, "3", like=True)

    with TestClient(app) as client:
        response = client.get("/api/recommend/alice")

    (call,) = models.calls
    assert [(entry.recipe_id, entry.like) for entry in call["history"]] == [("3", True)]
    assert [ingredient.fdc_id for ingredient in call["recipes"]["3"].ingredients] == [3]
    assert response.json()["profile"]["avg_price"] == pytest.approx(3.0)


def test_later_requests_are_served_from_the_cache(stores, models, cache):
    seed_alice(stores)

    with TestClient(app) as client:
        first = client.get("/api/recommend/alice", params={"top_k": 1}).json()
        second = client.get("/api/recommend/alice", params={"top_k": 3}).json()

    assert len(models.calls) == 1
    assert [item["id"] for item in first["items"]] == ["167516"]
    assert [item["id"] for item in second["items"]] == ["167516", "175237", "168516"]
    assert second["profile"] == first["profile"]


def test_user_lookup_goes_to_redis_before_the_database(stores, models, cache):
    alice = seed_alice(stores)
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
    bob = stores.create_user("bob")
    stores.add_history(bob.id, "openrecipes:3", like=True)

    with TestClient(app) as client:
        response = client.get("/api/recommend/bob")

    assert response.json() == {"profile": None, "items": []}
    assert len(models.calls) == 1
    assert f"recommendations:{bob.id}" in cache.store


def test_redis_down_still_answers_from_the_model(stores, models, cache):
    seed_alice(stores)
    cache.down = True

    with TestClient(app) as client:
        first = client.get("/api/recommend/alice")
        second = client.get("/api/recommend/alice")

    assert first.status_code == second.status_code == 200
    assert len(first.json()["items"]) == 3
    assert len(models.calls) == 2


def test_a_computed_recommendation_is_logged_and_a_cache_hit_at_debug(stores, models, cache, caplog):
    alice = seed_alice(stores)

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
    seed_alice(stores)
    cache.down = True

    with TestClient(app) as client:
        client.get("/api/recommend/alice")

    warnings = [record.getMessage() for record in caplog.records
                if record.name == "remymy_api.cache" and record.levelno == logging.WARNING]
    assert "Redis read of user:alice failed: down" in warnings
    assert any(message.startswith("Redis write of recommendations:") for message in warnings)


def test_without_a_catalog_the_api_starts_and_recommendations_are_503(stores, cache, monkeypatch):
    # the real load, on the catalog database conftest points at, which is not there
    monkeypatch.setattr(main, "load_recommender", load_recommender)
    alice = seed_alice(stores)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/api/recommend/alice")

    assert response.status_code == 503
    assert f"recommendations:{alice}" not in cache.store


def test_the_recommender_is_built_on_the_catalog_database(tmp_path, monkeypatch):
    from test_recipe_repository import food, write_catalog

    from remymy_api.inference import service
    from remymy_api.repository.recipe_repository import init_catalog_database

    database = tmp_path / "catalog.db"
    init_catalog_database(database, write_catalog(tmp_path / "catalog.csv", [food(1), food(2)]))
    monkeypatch.setenv("CATALOG_DATABASE_PATH", str(database))
    monkeypatch.setattr(service.Recommender, "load", classmethod(lambda cls, repository: repository.fdc_ids()))

    assert load_recommender() == {1, 2}


def test_a_disliked_pick_leaves_the_cached_recommendations(stores, models, repository, cache):
    alice = seed_alice(stores)
    key = f"recommendations:{alice}"

    with TestClient(app) as client:
        first = client.get("/api/recommend/alice", params={"top_k": 3}).json()
        # the cached entry has been there a while
        cache.ttls[key] = 1234
        disliked = client.put("/api/recipes/175237/rating", params={"user_id": alice, "like": False})
        liked = client.put("/api/recipes/168516/rating", params={"user_id": alice, "like": True})
        second = client.get("/api/recommend/alice", params={"top_k": 3}).json()

    assert disliked.status_code == liked.status_code == 200
    assert [item["id"] for item in first["items"]] == ["167516", "175237", "168516"]
    # served from the cache without the disliked one; the liked one stays
    assert [item["id"] for item in second["items"]] == ["167516", "168516"]
    assert len(models.calls) == 1
    assert cache.ttls[key] == 1234
    assert second["profile"] == first["profile"]


def test_a_dislike_is_saved_when_redis_is_down(stores, models, repository, cache):
    alice = seed_alice(stores)
    cache.down = True

    with TestClient(app) as client:
        response = client.put("/api/recipes/175237/rating", params={"user_id": alice, "like": False})

    assert response.status_code == 200
    assert response.json()["like"] is False



class ItemsRecommender:
    """Answers every recommendation with the same scored foods."""

    def __init__(self, items: list[dict]) -> None:
        self.items = items

    def recommend(self, history, recipes, top_k: int, preferences=None) -> list[dict]:
        return list(self.items)


SCORED = [
    {"fdc_id": 1, "name": "Steak", "price": 18.5, "p_like": 0.9, "similarity": 0.9},
    {"fdc_id": 2, "name": "Tofu", "price": 2.5, "p_like": 0.6, "similarity": 0.9},
    {"fdc_id": 3, "name": "Salmon", "price": 9.0, "p_like": 0.8, "similarity": 0.9},
    {"fdc_id": 4, "name": "Rice", "price": 2.0, "p_like": 0.6, "similarity": 0.9},
    {"fdc_id": 5, "name": "Prawns", "price": 12.0, "p_like": 0.7, "similarity": 0.9},
]


def recommended_ids(budget: float) -> list[str]:
    from remymy_api.schemas import Preferences, UserProfileInput

    user = UserProfileInput(user_id=1, preferences=Preferences(budget=budget), history=[])
    return [item.id for item in recommend.recommend(ItemsRecommender(SCORED), user, catalog=None).items]


def test_a_budget_keeps_only_what_it_affords_most_liked_first():
    # Steak and Prawns cost more than S$10; of Tofu and Rice, equally liked, the cheaper comes first
    assert recommended_ids(10) == ["3", "4", "2"]
    # the budget is inclusive
    assert recommended_ids(9) == ["3", "4", "2"]
    assert recommended_ids(1.5) == []


def test_no_budget_keeps_everything():
    assert recommended_ids(0) == ["1", "3", "5", "4", "2"]
