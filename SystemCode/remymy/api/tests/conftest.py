from __future__ import annotations

from collections.abc import Callable

import pytest
import redis

from remymy_api import main


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

    def set(self, key: str, value: object, ex: int | None = None) -> None:
        if self.down:
            raise redis.ConnectionError("down")
        self.store[key] = str(value).encode()
        self.ttls[key] = ex

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
        self.handler: Callable[[list[dict], int], dict] = lambda history, top_k: {
            "ingredients": [[] for _ in history],
            "items": [],
        }
        self.calls: list[dict] = []

    def recommend(self, history: list[dict], top_k: int) -> dict:
        self.calls.append({"history": history, "top_k": top_k})
        return self.handler(history, top_k)


@pytest.fixture(autouse=True)
def no_model(monkeypatch) -> None:
    """No test loads the real model and catalog unless it asks for them."""
    monkeypatch.setattr(main, "load_recommender", lambda: None)


@pytest.fixture
def recommender(monkeypatch) -> FakeRecommender:
    fake = FakeRecommender()
    monkeypatch.setattr(main, "load_recommender", lambda: fake)
    return fake
