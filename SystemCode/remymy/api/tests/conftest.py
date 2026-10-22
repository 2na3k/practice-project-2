from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest
import redis

from remymy_api import main
from remymy_api.history import History
from remymy_api.recipe import Recipe


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
    """No test loads the real models and catalog unless it asks for them. Startup builds the catalog database from
    CATALOG_PATH, so both point at files that are not there."""
    monkeypatch.setattr(main, "load_recommender", lambda: None)
    monkeypatch.setattr(main, "load_classifier", lambda: None)
    monkeypatch.setattr(main, "load_clusterer", lambda classifier: None)
    monkeypatch.setenv("CATALOG_PATH", str(tmp_path / "no-catalog.csv"))
    monkeypatch.setenv("CATALOG_DATABASE_PATH", str(tmp_path / "no-catalog.db"))


@pytest.fixture
def recommender(monkeypatch) -> FakeRecommender:
    fake = FakeRecommender()
    monkeypatch.setattr(main, "load_recommender", lambda: fake)
    return fake


@pytest.fixture
def override():
    """Swap a FastAPI dependency for the test, e.g. override(get_recipe_repository, lambda: fake)."""
    def swap(dependency, replacement) -> None:
        main.app.dependency_overrides[dependency] = replacement
    yield swap
    main.app.dependency_overrides.clear()
