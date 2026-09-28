from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from remymy_api import recommend
from remymy_api.bloom import BloomFilter
from remymy_api.main import app
from remymy_api.users import add_history, create_user, init_user_database, open_user_database


CATALOG = {
    "tomatoes": {"fdc_id": 1, "name": "Tomatoes, red, ripe", "price": 2.0,
                 "nutrient_values": {"energy_kcal": 18.0, "protein_g": 0.9}},
    "salt": {"fdc_id": 2, "name": "Salt, table", "price": 1.0,
             "nutrient_values": {"energy_kcal": 0.0, "sodium_mg": 38758.0}},
    "apple": {"fdc_id": 3, "name": "Apples, raw", "price": 3.0,
              "nutrient_values": {"energy_kcal": 52.0}},
}
RECOMMENDED = {"fdc_id": 167516, "name": "Waffles, buttermilk, frozen", "price": 6.85,
               "p_like": 0.8, "similarity": 0.9}


class FakeModels:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path: str, payload: dict) -> dict:
        self.calls.append((path, payload))
        if path == "/v1/ingredients/match":
            return {"matches": [CATALOG.get(text) for text in payload["ingredients"]]}
        return {"items": [RECOMMENDED]}

    def paths(self) -> list[str]:
        return [path for path, _ in self.calls]


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
def models(monkeypatch):
    fake = FakeModels()
    monkeypatch.setattr(recommend, "call_ml_models", fake)
    return fake


def test_profile_averages_every_bought_ingredient_and_feeds_the_model(stores, models):
    alice = create_user(stores, BloomFilter(capacity=1_000), "alice")
    add_history(stores, alice.id, "openrecipes:1", rating=4)
    add_history(stores, alice.id, "openrecipes:2")

    with TestClient(app) as client:
        response = client.get("/api/recommend/alice", params={"top_k": 5})

    assert response.status_code == 200
    body = response.json()
    assert body["profile"] == {
        "user_id": alice.id,
        "avg_price": pytest.approx(2.0),
        "avg_nutrient_values": {
            "energy_kcal": pytest.approx(70 / 3),
            "protein_g": pytest.approx(0.9),
            "sodium_mg": pytest.approx(38758.0),
        },
    }
    assert body["items"] == [{"id": "167516", "title": "Waffles, buttermilk, frozen", "score": 0.8}]

    assert models.paths() == ["/v1/ingredients/match", "/v1/recommend"]
    assert models.calls[0][1] == {"ingredients": ["apple", "salt", "tomatoes", "water"]}
    today = datetime.now(timezone.utc).date().isoformat()
    assert models.calls[1][1] == {
        "history": [
            {"fdc_id": 3, "rating": None, "date": today},
            {"fdc_id": 1, "rating": 4, "date": today},
            {"fdc_id": 2, "rating": 4, "date": today},
        ],
        "top_k": 5,
    }


def test_unknown_user_gets_nothing_without_calling_the_model(stores, models):
    with TestClient(app) as client:
        response = client.get("/api/recommend/nobody")

    assert response.json() == {"profile": None, "items": []}
    assert models.calls == []


def test_history_with_no_priced_ingredient_skips_inference(stores, models):
    bob = create_user(stores, BloomFilter(capacity=1_000), "bob")
    add_history(stores, bob.id, "openrecipes:3", rating=5)

    with TestClient(app) as client:
        response = client.get("/api/recommend/bob")

    assert response.json() == {"profile": None, "items": []}
    assert models.paths() == ["/v1/ingredients/match"]


def test_unreachable_model_service_is_503(stores, monkeypatch):
    monkeypatch.setenv("ML_MODELS_URL", "http://127.0.0.1:9")
    alice = create_user(stores, BloomFilter(capacity=1_000), "alice")
    add_history(stores, alice.id, "openrecipes:1", rating=4)

    with TestClient(app) as client:
        response = client.get("/api/recommend/alice")

    assert response.status_code == 503
    assert response.json() == {"detail": "Recommendations are unavailable."}
