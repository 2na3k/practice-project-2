from __future__ import annotations

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
