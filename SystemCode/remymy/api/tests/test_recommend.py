from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest
import redis
from fastapi.testclient import TestClient

from remymy_api import main, recommend
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
RECOMMENDED = [
    {"fdc_id": 167516, "name": "Waffles, buttermilk, frozen", "price": 6.85, "p_like": 0.8, "similarity": 0.9},
    {"fdc_id": 175237, "name": "Beans, black, cooked", "price": 2.72, "p_like": 0.7, "similarity": 0.9},
    {"fdc_id": 168516, "name": "Cabbage, savoy, cooked", "price": 4.2, "p_like": 0.6, "similarity": 0.9},
]


class FakeModels:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path: str, payload: dict) -> dict:
        self.calls.append((path, payload))
        matched = [
            [CATALOG[text] for text in dict.fromkeys(entry["ingredients"]) if text in CATALOG]
            for entry in payload["history"]
        ]
        return {"ingredients": matched, "items": RECOMMENDED if any(matched) else []}


class FakeCache:
    def __init__(self, down: bool = False) -> None:
        self.down = down
        self.store: dict[str, bytes] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key: str) -> bytes | None:
        if self.down:
            raise redis.ConnectionError("down")
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int) -> None:
        if self.down:
            raise redis.ConnectionError("down")
        self.store[key] = value.encode()
        self.ttls[key] = ex

    def close(self) -> None:
        pass


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


@pytest.fixture
def cache(monkeypatch):
    fake = FakeCache()
    monkeypatch.setattr(main, "connect_cache", lambda: fake)
    return fake


def seed_alice(stores) -> int:
    alice = create_user(stores, BloomFilter(capacity=1_000), "alice")
    add_history(stores, alice.id, "openrecipes:1", rating=4)
    add_history(stores, alice.id, "openrecipes:2")
    return alice.id


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

    today = datetime.now(timezone.utc).date().isoformat()
    assert models.calls == [(
        "/v1/recommend",
        {
            "history": [
                {"rating": None, "date": today, "ingredients": ["apple"]},
                {"rating": 4, "date": today, "ingredients": ["tomatoes", "salt", "salt", "water"]},
            ],
            "top_k": recommend.MAX_TOP_K,
        },
    )]

    cached = json.loads(cache.store[f"recommendations:{alice}"])
    assert len(cached["items"]) == 3
    assert cache.ttls[f"recommendations:{alice}"] == 3600


def test_later_requests_are_served_from_the_cache(stores, models, cache):
    seed_alice(stores)

    with TestClient(app) as client:
        first = client.get("/api/recommend/alice", params={"top_k": 1}).json()
        second = client.get("/api/recommend/alice", params={"top_k": 3}).json()

    assert len(models.calls) == 1
    assert [item["id"] for item in first["items"]] == ["167516"]
    assert [item["id"] for item in second["items"]] == ["167516", "175237", "168516"]
    assert second["profile"] == first["profile"]


def test_unknown_user_neither_calls_the_model_nor_caches(stores, models, cache):
    with TestClient(app) as client:
        response = client.get("/api/recommend/nobody")

    assert response.json() == {"profile": None, "items": []}
    assert models.calls == []
    assert cache.store == {}


def test_history_with_no_priced_ingredient_has_no_profile(stores, models, cache):
    bob = create_user(stores, BloomFilter(capacity=1_000), "bob")
    add_history(stores, bob.id, "openrecipes:3", rating=5)

    with TestClient(app) as client:
        response = client.get("/api/recommend/bob")

    assert response.json() == {"profile": None, "items": []}
    assert len(models.calls) == 1
    assert f"recommendations:{bob.id}" in cache.store


def test_redis_down_still_answers_from_the_model(stores, models, monkeypatch):
    monkeypatch.setattr(main, "connect_cache", lambda: FakeCache(down=True))
    seed_alice(stores)

    with TestClient(app) as client:
        first = client.get("/api/recommend/alice")
        second = client.get("/api/recommend/alice")

    assert first.status_code == second.status_code == 200
    assert len(first.json()["items"]) == 3
    assert len(models.calls) == 2


def test_unreachable_model_service_is_503_and_not_cached(stores, cache, monkeypatch):
    monkeypatch.setenv("ML_MODELS_URL", "http://127.0.0.1:9")
    seed_alice(stores)

    with TestClient(app) as client:
        response = client.get("/api/recommend/alice")

    assert response.status_code == 503
    assert response.json() == {"detail": "Recommendations are unavailable."}
    assert cache.store == {}
