from __future__ import annotations

import json
import random
import sqlite3
import string

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from remymy_api.bloom import BloomFilter
from remymy_api.main import app
from remymy_api.schemas import Preferences, UserHistory
from remymy_api.users import (
    add_history,
    create_user,
    fetch_user_history,
    init_user_database,
    load_username_filter,
    open_user_database,
    username_exists,
)


@pytest.fixture
def users(tmp_path, monkeypatch):
    recipes = tmp_path / "recipes.db"
    with sqlite3.connect(recipes) as connection:
        connection.executescript(
            """
            CREATE TABLE recipe (recipe_id TEXT PRIMARY KEY, title TEXT, quality_status TEXT NOT NULL);
            CREATE TABLE recipe_ingredient (recipe_id TEXT);
            CREATE TABLE recipe_instruction (recipe_id TEXT);
            INSERT INTO recipe VALUES
              ('openrecipes:1', 'Tomato Soup', 'accepted'),
              ('openrecipes:2', 'Apple Salad', 'accepted');
            """
        )
    connection.close()
    monkeypatch.setenv("DATABASE_PATH", str(recipes))
    monkeypatch.setenv("USER_DATABASE_PATH", str(tmp_path / "state" / "users.db"))
    init_user_database()
    connection = open_user_database()
    yield connection
    connection.close()


def queries(connection: sqlite3.Connection) -> list[str]:
    seen: list[str] = []
    connection.set_trace_callback(seen.append)
    return seen


def store(connection, document: object) -> None:
    connection.execute(
        "INSERT INTO app_user (username, preferences) VALUES ('probe', jsonb(?))",
        (json.dumps(document),),
    )


VALID = {"special_diet": "halal", "cuisines": ["Thai"], "preferred_nutrient": "fibre"}


@pytest.mark.parametrize(
    "document",
    [
        {k: v for k, v in VALID.items() if k != "cuisines"},
        {**VALID, "allergies": []},
        {**VALID, "special_diet": "vegan"},
        {**VALID, "special_diet": 1},
        {**VALID, "cuisines": "Thai"},
        {**VALID, "cuisines": [f"c{i}" for i in range(41)]},
        {**VALID, "cuisines": ["Thai", 7]},
        {**VALID, "preferred_nutrient": "x" * 65},
        {**VALID, "preferred_nutrient": ["fibre"]},
        ["not", "an", "object"],
    ],
)
def test_schema_rejects_preferences_outside_the_model(users, document):
    with pytest.raises(sqlite3.IntegrityError):
        store(users, document)


def test_schema_accepts_the_model_and_stores_binary_jsonb(users):
    store(users, {**VALID, "cuisines": [f"c{i}" for i in range(40)]})
    kind = users.execute("SELECT typeof(preferences) FROM app_user").fetchone()[0]
    assert kind == "blob"


def test_schema_rejects_preferences_stored_as_text(users):
    with pytest.raises(sqlite3.IntegrityError):
        users.execute(
            "INSERT INTO app_user (username, preferences) VALUES ('probe', ?)",
            (json.dumps(VALID),),
        )


def test_schema_default_preferences_are_valid(users):
    users.execute("INSERT INTO app_user (username) VALUES ('probe')")
    (raw,) = users.execute("SELECT json(preferences) FROM app_user").fetchone()
    assert Preferences.model_validate_json(raw) == Preferences()


def test_model_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        Preferences.model_validate({**VALID, "allergies": []})


def test_username_exists_miss_never_queries(users):
    usernames = BloomFilter(capacity=1_000)
    create_user(users, usernames, "alice")
    seen = queries(users)
    assert username_exists(users, usernames, "bob") is False
    assert seen == []


def test_username_exists_hit_is_confirmed(users):
    usernames = BloomFilter(capacity=1_000)
    create_user(users, usernames, "alice")
    seen = queries(users)
    assert username_exists(users, usernames, "alice") is True
    assert seen

    usernames.add("ghost")
    assert username_exists(users, usernames, "ghost") is False


def test_filter_loaded_at_startup_knows_existing_users(users):
    create_user(users, BloomFilter(capacity=1_000), "alice")
    assert username_exists(users, load_username_filter(users), "alice") is True


def test_duplicate_username_is_rejected(users):
    usernames = BloomFilter(capacity=1_000)
    create_user(users, usernames, "alice")
    with pytest.raises(sqlite3.IntegrityError):
        create_user(users, usernames, "alice")


def test_history_newest_first_and_limited(users):
    usernames = BloomFilter(capacity=1_000)
    alice = create_user(users, usernames, "alice")
    first = add_history(users, alice.id, "openrecipes:1", rating=4)
    second = add_history(users, alice.id, "openrecipes:2")

    history = fetch_user_history(users, usernames, "alice")
    assert history == [second, first]
    assert history[0].rating is None
    assert fetch_user_history(users, usernames, "alice", limit=1) == [second]


def test_history_for_unknown_username_never_queries(users):
    usernames = BloomFilter(capacity=1_000)
    create_user(users, usernames, "alice")
    seen = queries(users)
    assert fetch_user_history(users, usernames, "bob") == []
    assert seen == []


def test_history_only_shows_that_users_rows(users):
    usernames = BloomFilter(capacity=1_000)
    alice = create_user(users, usernames, "alice")
    create_user(users, usernames, "bob")
    add_history(users, alice.id, "openrecipes:1", rating=5)
    assert fetch_user_history(users, usernames, "bob") == []


def test_history_rejects_unknown_recipe(users):
    alice = create_user(users, BloomFilter(capacity=1_000), "alice")
    with pytest.raises(LookupError):
        add_history(users, alice.id, "openrecipes:999")


@pytest.mark.parametrize("rating", [-1, 6])
def test_history_rejects_rating_outside_zero_to_five(users, rating):
    alice = create_user(users, BloomFilter(capacity=1_000), "alice")
    with pytest.raises(sqlite3.IntegrityError):
        add_history(users, alice.id, "openrecipes:1", rating=rating)


def test_history_rejects_unknown_user(users):
    with pytest.raises(sqlite3.IntegrityError):
        add_history(users, 999, "openrecipes:1")


def test_history_model_rejects_rating_outside_zero_to_five():
    with pytest.raises(ValidationError):
        UserHistory(user_id=1, timestamp="2026-09-28T00:00:00Z", rating=6, recipe_id="x")


def test_startup_creates_the_database_and_filter(users):
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert "alice" not in client.app.state.usernames


def _names(count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choices(string.ascii_lowercase, k=12)) for _ in range(count)]


def test_bloom_has_no_false_negatives():
    bloom = BloomFilter(capacity=5_000, error_rate=0.01)
    added = _names(5_000, seed=1)
    for name in added:
        bloom.add(name)
    assert all(name in bloom for name in added)


def test_bloom_false_positive_rate_within_budget():
    bloom = BloomFilter(capacity=10_000, error_rate=0.01)
    for name in _names(10_000, seed=2):
        bloom.add(name)
    absent = _names(20_000, seed=99)
    rate = sum(name in bloom for name in absent) / len(absent)
    assert rate < 0.03
