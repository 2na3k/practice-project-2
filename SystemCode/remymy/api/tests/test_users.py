from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from remymy_api import users as users_module
from remymy_api.main import app
from remymy_api.schemas import Preferences, UserHistory
from remymy_api.users import (
    add_history,
    create_user,
    fetch_user_history,
    init_user_database,
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


def test_create_user_writes_the_user_id_to_redis(users, cache):
    alice = create_user(users, cache, "alice")
    assert cache.store == {"user:alice": str(alice.id).encode()}


def test_username_exists_answers_from_redis_without_querying(users, cache):
    create_user(users, cache, "alice")
    seen = queries(users)
    assert username_exists(users, cache, "alice") is True
    assert seen == []


def test_username_exists_falls_back_to_the_database_and_caches(users, cache):
    alice = create_user(users, cache, "alice")
    cache.store.clear()

    seen = queries(users)
    assert username_exists(users, cache, "alice") is True
    assert len(seen) == 1
    assert cache.store == {"user:alice": str(alice.id).encode()}

    seen.clear()
    assert username_exists(users, cache, "alice") is True
    assert seen == []


def test_unknown_username_is_not_cached(users, cache):
    assert username_exists(users, cache, "bob") is False
    assert cache.reads == ["user:bob"]
    assert cache.store == {}


def test_redis_down_answers_from_the_database(users, cache):
    create_user(users, cache, "alice")
    cache.down = True
    assert username_exists(users, cache, "alice") is True
    assert username_exists(users, cache, "bob") is False


def test_duplicate_username_is_rejected(users, cache):
    create_user(users, cache, "alice")
    with pytest.raises(sqlite3.IntegrityError):
        create_user(users, cache, "alice")


def test_history_newest_first_and_limited(users, cache):
    alice = create_user(users, cache, "alice")
    first = add_history(users, alice.id, "openrecipes:1", like=True)
    second = add_history(users, alice.id, "openrecipes:2", like=False)

    history = fetch_user_history(users, cache, "alice")
    assert history == [second, first]
    assert [entry.like for entry in history] == [False, True]
    assert fetch_user_history(users, cache, "alice", limit=1) == [second]


def test_history_for_unknown_username_is_empty(users, cache):
    create_user(users, cache, "alice")
    assert fetch_user_history(users, cache, "bob") == []


def test_history_only_shows_that_users_rows(users, cache):
    alice = create_user(users, cache, "alice")
    create_user(users, cache, "bob")
    add_history(users, alice.id, "openrecipes:1", like=True)
    assert fetch_user_history(users, cache, "bob") == []


def test_history_rejects_unknown_recipe(users, cache):
    alice = create_user(users, cache, "alice")
    with pytest.raises(LookupError):
        add_history(users, alice.id, "openrecipes:999", like=True)


@pytest.mark.parametrize("liked", [2, -1, None])
def test_history_table_holds_only_liked_or_not(users, cache, liked):
    alice = create_user(users, cache, "alice")
    with pytest.raises(sqlite3.IntegrityError):
        users.execute("INSERT INTO user_history (user_id, liked, recipe_id) VALUES (?, ?, 'openrecipes:1')",
                      (alice.id, liked))


def test_history_rejects_unknown_user(users, cache):
    with pytest.raises(sqlite3.IntegrityError):
        add_history(users, 999, "openrecipes:1", like=True)


@pytest.mark.parametrize("like", [None, "maybe"])
def test_history_model_needs_like_or_not(like):
    with pytest.raises(ValidationError):
        UserHistory(user_id=1, timestamp="2026-09-28T00:00:00Z", like=like, recipe_id="x")


def test_a_database_with_ratings_keeps_them_as_likes(tmp_path, monkeypatch):
    path = tmp_path / "old-users.db"
    liked_column = "  liked      INTEGER NOT NULL CHECK (liked IN (0, 1)),  -- UserHistory.like: 1 liked, 0 not\n"
    assert liked_column in users_module.SCHEMA
    # the schema as it was before likes
    old_schema = users_module.SCHEMA.replace(
        liked_column, "  rating     INTEGER CHECK (rating IS NULL OR rating IN (0, 1, 2, 3, 4, 5)),\n"
    )
    with sqlite3.connect(path) as connection:
        connection.executescript(old_schema + """
            INSERT INTO app_user (user_id, username) VALUES (1, 'alice');
            INSERT INTO user_history (user_id, rating, recipe_id) VALUES
              (1, 5, 'openrecipes:1'), (1, 3, 'openrecipes:2'), (1, 2, 'openrecipes:3'), (1, NULL, 'openrecipes:4');
            """)
    connection.close()
    monkeypatch.setenv("USER_DATABASE_PATH", str(path))

    init_user_database()
    init_user_database()

    connection = open_user_database()
    try:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(user_history)")}
        rows = connection.execute("SELECT recipe_id, liked FROM user_history ORDER BY history_id").fetchall()
    finally:
        connection.close()
    assert "rating" not in columns
    # 3 to 5 liked, as the model's training labels them; 0 to 2 or no rating not
    assert [tuple(row) for row in rows] == [
        ("openrecipes:1", 1), ("openrecipes:2", 1), ("openrecipes:3", 0), ("openrecipes:4", 0),
    ]


def test_rating_a_recipe_adds_a_like_to_the_history(users, cache):
    alice = create_user(users, cache, "alice")

    with TestClient(app) as client:
        liked = client.put("/api/recipes/openrecipes:1/rating", params={"user_id": alice.id, "like": True})
        disliked = client.put("/api/recipes/openrecipes:2/rating", params={"user_id": alice.id, "like": False})

    assert liked.status_code == disliked.status_code == 200
    assert liked.json()["like"] is True and liked.json()["recipe_id"] == "openrecipes:1"
    assert [(entry.recipe_id, entry.like) for entry in fetch_user_history(users, cache, "alice")] == [
        ("openrecipes:2", False), ("openrecipes:1", True),
    ]


def test_rating_an_unknown_recipe_or_user_is_404(users, cache):
    alice = create_user(users, cache, "alice")

    with TestClient(app) as client:
        recipe = client.put("/api/recipes/openrecipes:999/rating", params={"user_id": alice.id, "like": True})
        user = client.put("/api/recipes/openrecipes:1/rating", params={"user_id": 999, "like": True})
        invalid = client.put("/api/recipes/openrecipes:1/rating", params={"user_id": alice.id, "like": "maybe"})

    assert (recipe.status_code, recipe.json()) == (404, {"detail": "Recipe not found."})
    assert (user.status_code, user.json()) == (404, {"detail": "User not found."})
    assert invalid.status_code == 422
    assert fetch_user_history(users, cache, "alice") == []


def test_startup_creates_the_database_and_cache(users, cache):
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.app.state.cache is cache


def test_a_user_is_looked_up_by_username(users, cache):
    alice = create_user(users, cache, "alice", Preferences(special_diet="halal", cuisines=["Thai"]))

    with TestClient(app) as client:
        found = client.get("/api/users/alice")
        missing = client.get("/api/users/nobody")

    assert found.status_code == 200
    assert found.json() == {
        "id": alice.id, "username": "alice",
        "preferences": {"special_diet": "halal", "cuisines": ["Thai"], "preferred_nutrient": None},
    }
    assert (missing.status_code, missing.json()) == (404, {"detail": "User not found."})


class OneFoodCatalog:
    """Stands in for the shared recipe_repository: its only food is fdc_id 169286."""

    def food(self, fdc_id: int) -> object | None:
        return object() if fdc_id == 169286 else None


def test_rating_again_changes_the_like_and_dates_it_now(users, cache):
    alice = create_user(users, cache, "alice")
    add_history(users, alice.id, "openrecipes:1", like=True)
    with users:
        users.execute("UPDATE user_history SET timestamp = '2026-01-01T00:00:00.000Z'")

    again = add_history(users, alice.id, "openrecipes:1", like=False)

    assert fetch_user_history(users, cache, "alice") == [again]
    assert again.like is False
    assert again.timestamp > datetime(2026, 1, 2, tzinfo=timezone.utc)


def test_a_catalog_food_is_rated_by_its_fdc_id(users, cache, monkeypatch):
    alice = create_user(users, cache, "alice")
    monkeypatch.setattr(users_module, "recipe_repository", OneFoodCatalog())

    assert add_history(users, alice.id, "169286", like=True).recipe_id == "169286"
    with pytest.raises(LookupError):
        add_history(users, alice.id, "999", like=True)
    # without a catalog no food is known
    monkeypatch.setattr(users_module, "recipe_repository", None)
    with pytest.raises(LookupError):
        add_history(users, alice.id, "169286", like=False)
