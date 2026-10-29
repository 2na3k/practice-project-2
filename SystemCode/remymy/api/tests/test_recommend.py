from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pytest
from conftest import add_recipes
from fastapi.testclient import TestClient

from sqlalchemy.orm import Session

from remymy_api import main, recommend
from remymy_api.cache import RATINGS_BEFORE_REFRESH
from remymy_api.db import database_engine
from remymy_api.dependencies import get_recipe_repository
from remymy_api.main import app, load_recommender
from remymy_api.recipe import DigestedRecipe
from remymy_api.repository.user_repository import UserRepository
from remymy_api.schemas import Ingredient
from remymy_api.schema import init_database


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

    def digest(self, recipe_id: str, lines: list[str]) -> DigestedRecipe:
        self.digested.append(recipe_id)
        return DigestedRecipe(recipe_id, [Ingredient(**CATALOG[line]) for line in dict.fromkeys(lines) if line in CATALOG])

    def food(self, fdc_id: int) -> dict | None:
        found = next((food for food in CATALOG.values() if food["fdc_id"] == fdc_id), None)
        if found is None:
            # a recommended food, as the groceries panel rates it
            found = next(({"fdc_id": item["fdc_id"], "name": item["name"], "price": item["price"], "nutrient_values": {}}
                          for item in RECOMMENDED if item["fdc_id"] == fdc_id), None)
        return found

    def digest_food(self, food_id: str) -> DigestedRecipe:
        self.digested.append(food_id)
        food = self.food(int(food_id))
        return DigestedRecipe(food_id, [] if food is None else [Ingredient(**food)])


def answer(history, recipes, top_k: int) -> list[dict]:
    return RECOMMENDED if any(recipes[entry.recipe_id].ingredients for entry in history) else []


@pytest.fixture
def stores(tmp_path, monkeypatch, cache):
    """The database with three recipes, and a UserRepository on it."""
    add_recipes(
        [
            ("openrecipes:1", "Tomato Soup", "accepted"),
            ("openrecipes:2", "Apple Salad", "accepted"),
            ("openrecipes:3", "Plain Water", "accepted"),
        ],
        [
            ("openrecipes:1", 1, "2 cups tomatoes", "tomatoes"),
            ("openrecipes:1", 2, "1 tsp salt", "salt"),
            ("openrecipes:1", 3, "salt to taste", "salt"),
            ("openrecipes:1", 4, "4 cups water", "water"),
            ("openrecipes:2", 1, "apple", None),
            ("openrecipes:3", 1, "1 cup water", "water"),
        ],
    )
    init_database()
    with Session(database_engine()) as session:
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


FILLER = "openrecipes:filler-"
POPULAR = [{"fdc_id": 175237, "name": "Beans, black, cooked", "price": 2.72},
           {"fdc_id": 168516, "name": "Cabbage, savoy, cooked", "price": 4.2}]


def rate_past_cold_start(stores, user_id: int) -> None:
    """RATINGS_BEFORE_REFRESH ratings of recipes no priced ingredient matches, so that with one more the user is past
    cold start and the model recommends for them. They change neither the profile nor what the model answers."""
    for number in range(RATINGS_BEFORE_REFRESH):
        stores.add_history(user_id, f"{FILLER}{number}", like=True)


def seed_alice(stores) -> int:
    alice = stores.create_user("alice")
    rate_past_cold_start(stores, alice.id)
    stores.add_history(alice.id, "openrecipes:1", like=True)
    stores.add_history(alice.id, "openrecipes:2", like=False)
    return alice.id


def test_a_new_user_gets_the_most_rated_foods_their_preferences_allow_not_the_model(stores, models, cache):
    from remymy_api.schemas import Preferences

    seed_alice(stores)
    bob = stores.create_user("bob", Preferences(special_diet="halal", budget=8.5))
    stores.add_history(bob.id, "openrecipes:1", like=False)
    carol = stores.create_user("carol")
    models.cold_start = POPULAR

    with TestClient(app) as client:
        response = client.get("/api/recommend/carol", params={"top_k": 1})
        client.get("/api/recommend/bob")

    assert response.status_code == 200
    assert response.json() == {"profile": None, "items": [{"id": "175237", "title": "Beans, black, cooked",
                                                           "score": 0.0}], "source": "popular"}
    first, second = models.cold_start_calls
    # how many users rated each item, liked or not: alice and bob both rated openrecipes:1
    assert first["rating_counts"]["openrecipes:1"] == 2 and first["rating_counts"]["openrecipes:2"] == 1
    assert first["history"].user_id == carol.id and len(first["history"]) == 0
    assert (second["preferences"]["special_diet"], second["preferences"]["budget"]) == ("halal", 8.5)
    assert models.calls == []
    # not cached: the model takes over as soon as the history is past cold start
    assert f"recommendations:{bob.id}" not in cache.store


def test_cold_start_reports_the_historys_profile_and_hands_its_ratings_over(stores, models, cache):
    alice = stores.create_user("alice")
    stores.add_history(alice.id, "openrecipes:1", like=True)
    models.cold_start = POPULAR

    with TestClient(app) as client:
        body = client.get("/api/recommend/alice").json()

    assert body["source"] == "popular"
    # tomatoes 2.0 and salt 1.0, as the model's recommendations would report it
    assert body["profile"]["avg_price"] == pytest.approx(1.5)
    (call,) = models.cold_start_calls
    # the rated recipe goes over for cold_start_items to leave out
    assert [(entry.recipe_id, entry.like) for entry in call["history"]] == [("openrecipes:1", True)]


def test_the_model_recommends_once_more_than_the_refresh_count_is_rated(stores, models, repository, cache):
    alice = stores.create_user("alice")
    rate_past_cold_start(stores, alice.id)
    models.cold_start = POPULAR

    with TestClient(app) as client:
        cold = client.get("/api/recommend/alice").json()
        assert client.put("/api/recipes/175237/rating", params={"user_id": alice.id, "like": True}).status_code == 200
        warm = client.get("/api/recommend/alice").json()

    # RATINGS_BEFORE_REFRESH ratings: still the most rated; one more, and the model recommends
    assert cold["source"] == "popular" and len(models.cold_start_calls) == 1
    assert warm["source"] == "model" and [item["id"] for item in warm["items"]] == ["167516", "175237", "168516"]
    assert len(models.calls) == 1


def test_without_a_catalog_a_new_user_gets_503_too(stores, cache, monkeypatch):
    monkeypatch.setattr(main, "load_recommender", load_recommender)
    stores.create_user("bob")

    with TestClient(app) as client:
        assert client.get("/api/recommend/bob").status_code == 503


def test_the_users_preferences_reach_the_recommender(stores, models):
    from remymy_api.schemas import Preferences

    bob = stores.create_user("bob", Preferences(special_diet="halal", preferred_nutrient="protein", budget=8.5))
    rate_past_cold_start(stores, bob.id)
    stores.add_history(bob.id, "openrecipes:1", like=True)

    with TestClient(app) as client:
        assert client.get("/api/recommend/bob").status_code == 200

    (call,) = models.calls
    assert call["preferences"] == {"special_diet": "halal", "cuisines": [], "preferred_nutrient": "protein",
                                   "budget": 8.5, "allergies": []}


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
    assert len(history) == RATINGS_BEFORE_REFRESH + 2
    # oldest first, after the fillers; each recipe's lines matched once ("salt" twice, "water" to nothing)
    rated = list(history)[-2:]
    assert [(entry.recipe_id, entry.like) for entry in rated] == [
        ("openrecipes:1", True), ("openrecipes:2", False),
    ]
    recipes = call["recipes"]
    assert [[ingredient.fdc_id for ingredient in recipes[entry.recipe_id].ingredients] for entry in rated] == [
        [1, 2], [3],
    ]
    today = datetime.now(timezone.utc).date()
    assert all(entry.timestamp.date() == today for entry in history)

    cached = json.loads(cache.store[f"recommendations:{alice}"])
    assert len(cached["items"]) == 3
    assert cache.ttls[f"recommendations:{alice}"] == 86400


def test_rating_a_recipe_again_changes_its_like(stores, models, repository, cache):
    alice = stores.create_user("alice")
    rate_past_cold_start(stores, alice.id)
    stores.add_history(alice.id, "openrecipes:1", like=False)
    stores.add_history(alice.id, "openrecipes:1", like=True)

    with TestClient(app) as client:
        response = client.get("/api/recommend/alice")

    assert response.status_code == 200
    (call,) = models.calls
    assert len(call["history"]) == RATINGS_BEFORE_REFRESH + 1
    assert [(entry.recipe_id, entry.like) for entry in call["history"]][-1:] == [("openrecipes:1", True)]
    assert [recipe for recipe in repository.digested if not recipe.startswith(FILLER)] == ["openrecipes:1"]
    # tomatoes 2.0 and salt 1.0
    assert response.json()["profile"]["avg_price"] == pytest.approx(1.5)


def test_a_rated_grocery_goes_to_the_model_as_itself(stores, models, repository, cache):
    alice = stores.create_user("alice")
    rate_past_cold_start(stores, alice.id)
    # "Apples, raw" of CATALOG, by its fdc_id, as the groceries panel rates it
    stores.add_history(alice.id, "3", like=True)

    with TestClient(app) as client:
        response = client.get("/api/recommend/alice")

    (call,) = models.calls
    assert [(entry.recipe_id, entry.like) for entry in call["history"]][-1:] == [("3", True)]
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

    assert response.json() == {"profile": None, "items": [], "source": "model"}
    assert models.calls == [] and models.cold_start_calls == []
    assert cache.store == {}


def test_history_with_no_priced_ingredient_has_no_profile(stores, models, cache):
    bob = stores.create_user("bob")
    rate_past_cold_start(stores, bob.id)
    stores.add_history(bob.id, "openrecipes:3", like=True)

    with TestClient(app) as client:
        response = client.get("/api/recommend/bob")

    assert response.json() == {"profile": None, "items": [], "source": "model"}
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
    assert computed[0].getMessage().startswith(
        f"Recommended 3 foods for user {alice} from {RATINGS_BEFORE_REFRESH + 2} history recipes in ")
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


def test_the_recommender_is_built_on_the_databases_catalog(tmp_path, monkeypatch):
    from test_recipe_repository import food, write_catalog

    from remymy_api.inference import service
    from remymy_api.repository.recipe_repository import init_catalog_database

    database = tmp_path / "remymy.db"
    init_catalog_database(database, write_catalog(tmp_path / "catalog.csv", [food(1), food(2)]))
    monkeypatch.setenv("DATABASE_PATH", str(database))
    monkeypatch.setattr(service.Recommender, "_load",
                        lambda self, repository: setattr(self, "fdc_ids", repository.fdc_ids()))

    assert load_recommender().fdc_ids == {1, 2}


def test_the_recommendations_are_refreshed_once_more_than_the_refresh_count_of_ratings_is_counted(stores, models, repository, cache):
    alice = seed_alice(stores)
    recommendations = f"recommendations:{alice}"
    ratings = f"number_of_ratings:{alice}"

    with TestClient(app) as client:
        client.get("/api/recommend/alice", params={"top_k": 3})
        for number in range(RATINGS_BEFORE_REFRESH):
            like = number % 2 == 1
            assert client.put("/api/recipes/175237/rating", params={"user_id": alice, "like": like}).status_code == 200
        # RATINGS_BEFORE_REFRESH ratings, likes and dislikes alike, each counted and each keeping the count a day from then
        counted = int(cache.store[ratings]), cache.ttls[ratings]
        still_cached = recommendations in cache.store
        client.get("/api/recommend/alice", params={"top_k": 3})
        calls_before_refresh = len(models.calls)
        client.put("/api/recipes/168516/rating", params={"user_id": alice, "like": True})
        # one more drops the recommendations and the count
        refreshed = recommendations not in cache.store and ratings not in cache.store
        client.get("/api/recommend/alice", params={"top_k": 3})
        client.put("/api/recipes/168516/rating", params={"user_id": alice, "like": False})

    assert counted == (RATINGS_BEFORE_REFRESH, 86400)
    assert still_cached and calls_before_refresh == 1
    assert refreshed
    assert len(models.calls) == 2
    # the next rating counts from 1 again
    assert int(cache.store[ratings]) == 1


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
