from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import pytest
import redis

from remymy_api import main
from remymy_api.db import database_path
from remymy_api.history import History
from remymy_api.dependencies import get_recommender
from remymy_api.inference.classification_service import FoodClassifier
from remymy_api.inference.food_query import TermExtractor
from remymy_api.inference.service import Clusterer, Recommender
from remymy_api.recipe import Recipe
from remymy_api.schema import FOOD_SCHEMA, connect, init_database


class FakeCache:
    def __init__(self) -> None:
        self.down = False
        self.store: dict[str, bytes] = {}
        self.ttls: dict[str, int | None] = {}
        self.reads: list[str] = []

    def get(self, key: str) -> bytes | None:
        if self.down:
            raise redis.ConnectionError("down")
        self.reads.append(key)
        return self.store.get(key)

    def ttl(self, key: str) -> int:
        if self.down:
            raise redis.ConnectionError("down")
        # as Redis answers: -2 for a missing key, -1 for one without an expiry
        return -2 if key not in self.store else (self.ttls.get(key) or -1)

    def set(self, key: str, value: object, ex: int | None = None) -> None:
        if self.down:
            raise redis.ConnectionError("down")
        self.store[key] = str(value).encode()
        self.ttls[key] = ex

    def incr(self, key: str) -> int:
        if self.down:
            raise redis.ConnectionError("down")
        value = int(self.store.get(key, b"0")) + 1
        self.store[key] = str(value).encode()
        return value

    def expire(self, key: str, seconds: int) -> bool:
        if self.down:
            raise redis.ConnectionError("down")
        if key not in self.store:
            return False
        self.ttls[key] = seconds
        return True

    def delete(self, key: str) -> int:
        if self.down:
            raise redis.ConnectionError("down")
        self.ttls.pop(key, None)
        return 0 if self.store.pop(key, None) is None else 1

    def close(self) -> None:
        pass


@pytest.fixture
def cache(monkeypatch) -> FakeCache:
    fake = FakeCache()
    monkeypatch.setattr(main, "connect_cache", lambda: fake)
    return fake


class FakeRecommender:
    """Stands in for inference.service.Recommender, recording what each recommend call was given."""

    def __init__(self) -> None:
        self.handler: Callable[[History, Mapping[str, Recipe], int], list[dict]] = (
            lambda history, recipes, top_k: []
        )
        self.calls: list[dict] = []

    def recommend(self, history: History, recipes: Mapping[str, Recipe], top_k: int,
                  preferences: Mapping | None = None) -> list[dict]:
        self.calls.append({"history": history, "recipes": recipes, "top_k": top_k, "preferences": preferences})
        return self.handler(history, recipes, top_k)


@pytest.fixture(autouse=True)
def no_model(monkeypatch, tmp_path) -> None:
    """No test loads the real models and catalog unless it asks for them. Startup fills the catalog from CATALOG_PATH,
    which points at a file that is not there. Each test has a remymy.db of its own, and no old databases to import."""
    monkeypatch.setattr(main, "load_term_extractor", lambda: None)
    monkeypatch.setattr(main, "load_recommender", lambda: None)
    monkeypatch.setattr(main, "load_classifier", lambda: None)
    monkeypatch.setattr(main, "load_clusterer", lambda: None)
    # the (fake) models load before startup ends, so a test's first request finds them loaded; test_startup.py checks
    # the background thread the API loads them on
    monkeypatch.setattr(main, "start_model_loading", main.load_models)
    # each test starts without the services' instances, so one a test constructs does not answer for the next
    for service in (Recommender, FoodClassifier, Clusterer, TermExtractor):
        monkeypatch.setattr(service, "_instance", None)
    monkeypatch.setenv("CATALOG_PATH", str(tmp_path / "no-catalog.csv"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "state" / "remymy.db"))
    monkeypatch.setenv("LEGACY_FOOD_DATABASE_PATH", str(tmp_path / "no-legacy-food.db"))
    monkeypatch.setenv("LEGACY_USER_DATABASE_PATH", str(tmp_path / "no-legacy-users.db"))


def add_recipes(
    recipes: Iterable[tuple[str, str | None, str]],
    ingredients: Iterable[tuple[str, int, str, str | None]] = (),
    instructions: Iterable[tuple[str, int, str]] = (),
    path: Path | None = None,
) -> Path:
    """Put Food.com recipes in the database at path (DATABASE_PATH by default), as the migration job would: each
    (recipe_id, title, quality_status) with a source record, its ingredient lines (recipe_id, position, raw_text,
    ingredient_text) and its steps (recipe_id, position, text). Creates the food tables where missing, so it can run
    before startup does."""
    path = path or database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(path)
    try:
        connection.executescript(FOOD_SCHEMA)
        with connection:
            connection.execute(
                "INSERT OR IGNORE INTO source_snapshot (snapshot_id, source_name, source_version, source_uri, sha256, "
                "retrieved_at, schema_hash) VALUES ('test', 'test', '1', 'test', '0', '2026-01-01T00:00:00Z', '0')"
            )
            for recipe_id, title, quality_status in recipes:
                connection.execute(
                    "INSERT INTO source_record (source_record_id, snapshot_id, source_native_id, record_type, "
                    "raw_payload, raw_hash) VALUES (?, 'test', ?, 'recipe', '{}', '0')",
                    (f"{recipe_id}:source", recipe_id),
                )
                connection.execute(
                    "INSERT INTO recipe (recipe_id, source_snapshot_id, source_record_id, title, quality_status, "
                    "raw_hash, normalization_version) VALUES (?, 'test', ?, ?, ?, '0', 'test')",
                    (recipe_id, f"{recipe_id}:source", title, quality_status),
                )
            for recipe_id, position, raw_text, ingredient_text in ingredients:
                connection.execute(
                    "INSERT INTO recipe_ingredient (ingredient_id, recipe_id, position, raw_text, ingredient_text, "
                    "parse_status) VALUES (?, ?, ?, ?, ?, 'parsed')",
                    (f"{recipe_id}:ingredient:{position}", recipe_id, position, raw_text, ingredient_text),
                )
            for recipe_id, position, text in instructions:
                connection.execute("INSERT INTO recipe_instruction (recipe_id, position, text) VALUES (?, ?, ?)",
                                   (recipe_id, position, text))
    finally:
        connection.close()
    return path


@pytest.fixture
def database() -> Path:
    """The test's remymy.db, set up as startup sets it up: its tables, and no recipes, users or catalog."""
    init_database()
    return database_path()


@pytest.fixture
def recommender() -> FakeRecommender:
    fake = FakeRecommender()
    main.app.dependency_overrides[get_recommender] = lambda: fake
    yield fake
    main.app.dependency_overrides.pop(get_recommender, None)


@pytest.fixture
def override():
    """Swap a FastAPI dependency for the test, e.g. override(get_recipe_repository, lambda: fake)."""
    def swap(dependency, replacement) -> None:
        main.app.dependency_overrides[dependency] = replacement
    yield swap
    main.app.dependency_overrides.clear()
