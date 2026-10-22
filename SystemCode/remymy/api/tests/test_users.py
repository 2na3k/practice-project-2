from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from conftest import add_recipes
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from remymy_api import users as users_module
from remymy_api.db import database_engine, database_path
from remymy_api.dependencies import get_recipe_repository
from remymy_api.main import app
from remymy_api.repository.user_repository import UserRepository
from remymy_api.schemas import Preferences, UserRating
from remymy_api.schema import connect, init_database
from remymy_api.users import rate_item


@pytest.fixture
def users(tmp_path, monkeypatch):
    """A plain connection to a new database, for the SQL schema's own tests. It holds the recipes openrecipes:1 and
    openrecipes:2."""
    add_recipes([("openrecipes:1", "Tomato Soup", "accepted"), ("openrecipes:2", "Apple Salad", "accepted")])
    init_database()
    connection = connect(database_path())
    yield connection
    connection.close()


@pytest.fixture
def repo(users, cache):
    """The UserRepository on a session of that database."""
    with Session(database_engine()) as session:
        yield UserRepository(session, cache)


@pytest.fixture
def sql(users):
    """The statements the ORM sends to the users database during the test."""
    seen: list[str] = []

    def record(connection, cursor, statement, parameters, context, executemany) -> None:
        seen.append(statement)

    engine = database_engine()
    event.listen(engine, "before_cursor_execute", record)
    yield seen
    event.remove(engine, "before_cursor_execute", record)


def store(connection, document: object) -> None:
    connection.execute(
        "INSERT INTO app_user (username, preferences) VALUES ('probe', jsonb(?))",
        (json.dumps(document),),
    )


VALID = {"special_diet": "halal", "cuisines": ["Thai"], "preferred_nutrient": "fibre", "allergies": ["peanut"]}


@pytest.mark.parametrize(
    "document",
    [
        {**VALID, "special_diet": "vegan"},
        {**VALID, "special_diet": 1},
        {**VALID, "cuisines": "Thai"},
        {**VALID, "cuisines": [f"c{i}" for i in range(41)]},
        {**VALID, "cuisines": ["Thai", 7]},
        {**VALID, "preferred_nutrient": "x" * 65},
        {**VALID, "preferred_nutrient": ["fibre"]},
        {**VALID, "budget": 5},
        {**VALID, "budget": -1.5},
        {**VALID, "allergies": "peanut"},
        {**VALID, "allergies": ["peanut", "nuts"]},
        {**VALID, "allergies": ["peanut", 7]},
        {**VALID, "allergies": ["peanut"] * 13},
        ["not", "an", "object"],
    ],
)
def test_schema_rejects_preferences_outside_the_model(users, document):
    with pytest.raises(sqlite3.IntegrityError):
        store(users, document)


@pytest.mark.parametrize("field", ["special_diet", "cuisines", "preferred_nutrient", "budget", "allergies"])
def test_schema_accepts_each_field_missing_or_null(users, field):
    store(users, {k: v for k, v in VALID.items() if k != field})
    users.execute("DELETE FROM app_user")
    store(users, {**VALID, field: None})


def test_model_skips_null_fields_to_their_defaults():
    assert Preferences.model_validate(
        {"special_diet": None, "cuisines": None, "preferred_nutrient": None, "budget": None, "allergies": None}
    ) == Preferences()


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
        Preferences.model_validate({**VALID, "favourite_colour": "red"})


def test_created_preferences_are_stored_as_binary_jsonb_and_read_back(users, repo):
    alice = repo.create_user("alice", Preferences(special_diet="halal", cuisines=["Thai"]))

    assert users.execute("SELECT typeof(preferences) FROM app_user").fetchone()[0] == "blob"
    assert repo.get_user("alice") == alice
    assert repo.get_user("nobody") is None


def test_create_user_writes_the_user_id_to_redis(repo, cache):
    alice = repo.create_user("alice")
    assert cache.store == {"user:alice": str(alice.id).encode()}


def test_username_exists_answers_from_redis_without_querying(repo, sql):
    repo.create_user("alice")
    sql.clear()
    assert repo.username_exists("alice") is True
    assert sql == []


def test_username_exists_falls_back_to_the_database_and_caches(repo, cache, sql):
    alice = repo.create_user("alice")
    cache.store.clear()

    sql.clear()
    assert repo.username_exists("alice") is True
    assert len(sql) == 1
    assert cache.store == {"user:alice": str(alice.id).encode()}

    sql.clear()
    assert repo.username_exists("alice") is True
    assert sql == []


def test_unknown_username_is_not_cached(repo, cache):
    assert repo.username_exists("bob") is False
    assert cache.reads == ["user:bob"]
    assert cache.store == {}


def test_redis_down_answers_from_the_database(repo, cache):
    repo.create_user("alice")
    cache.down = True
    assert repo.username_exists("alice") is True
    assert repo.username_exists("bob") is False


def test_duplicate_username_is_rejected_and_the_repository_still_works(repo):
    repo.create_user("alice")
    with pytest.raises(IntegrityError):
        repo.create_user("alice")
    assert repo.create_user("bob").username == "bob"


def test_history_newest_first_and_limited(repo):
    alice = repo.create_user("alice")
    first = repo.add_history(alice.id, "openrecipes:1", like=True)
    second = repo.add_history(alice.id, "openrecipes:2", like=False)

    history = repo.fetch_user_history("alice")
    assert history == [second, first]
    assert [entry.like for entry in history] == [False, True]
    assert repo.fetch_user_history("alice", limit=1) == [second]


def test_history_for_unknown_username_is_empty(repo):
    repo.create_user("alice")
    assert repo.fetch_user_history("bob") == []


def test_history_only_shows_that_users_rows(repo):
    alice = repo.create_user("alice")
    repo.create_user("bob")
    repo.add_history(alice.id, "openrecipes:1", like=True)
    assert repo.fetch_user_history("bob") == []


def test_rating_rejects_an_unknown_recipe(repo):
    alice = repo.create_user("alice")
    with pytest.raises(LookupError):
        rate_item(repo, None, alice.id, "openrecipes:999", like=True)
    assert repo.fetch_user_history("alice") == []


@pytest.mark.parametrize("liked", [2, -1, None])
def test_history_table_holds_only_liked_or_not(users, repo, liked):
    alice = repo.create_user("alice")
    with pytest.raises(sqlite3.IntegrityError):
        users.execute("INSERT INTO user_history (user_id, liked, recipe_id) VALUES (?, ?, 'openrecipes:1')",
                      (alice.id, liked))


def test_history_rejects_unknown_user(repo):
    with pytest.raises(IntegrityError):
        repo.add_history(999, "openrecipes:1", like=True)


@pytest.mark.parametrize("like", [None, "maybe"])
def test_history_model_needs_like_or_not(like):
    with pytest.raises(ValidationError):
        UserRating(user_id=1, timestamp="2026-09-28T00:00:00Z", like=like, recipe_id="x")


def test_the_old_users_database_is_imported_once_with_its_ratings_as_likes(tmp_path, monkeypatch):
    path = tmp_path / "old-users.db"
    liked_column = "  liked      INTEGER NOT NULL CHECK (liked IN (0, 1)),  -- UserRating.like: 1 liked, 0 not\n"
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
    monkeypatch.setenv("LEGACY_USER_DATABASE_PATH", str(path))

    init_database()
    connection = connect(database_path())
    try:
        users = [tuple(row) for row in connection.execute("SELECT user_id, username FROM app_user")]
        rows = connection.execute("SELECT recipe_id, liked FROM user_history ORDER BY history_id").fetchall()
        # once imported, never again: a user deleted since stays deleted
        with connection:
            connection.execute("DELETE FROM app_user")
    finally:
        connection.close()
    init_database()
    connection = connect(database_path())
    try:
        left = connection.execute("SELECT count(*) FROM app_user").fetchone()[0]
    finally:
        connection.close()

    assert users == [(1, "alice")]
    # 3 to 5 liked, as the model's training labels them; 0 to 2 or no rating not
    assert [tuple(row) for row in rows] == [
        ("openrecipes:1", 1), ("openrecipes:2", 1), ("openrecipes:3", 0), ("openrecipes:4", 0),
    ]
    assert left == 0


def test_rating_a_recipe_adds_a_like_to_the_history(repo):
    alice = repo.create_user("alice")

    with TestClient(app) as client:
        liked = client.put("/api/recipes/openrecipes:1/rating", params={"user_id": alice.id, "like": True})
        disliked = client.put("/api/recipes/openrecipes:2/rating", params={"user_id": alice.id, "like": False})

    assert liked.status_code == disliked.status_code == 200
    assert liked.json()["like"] is True and liked.json()["recipe_id"] == "openrecipes:1"
    assert [(entry.recipe_id, entry.like) for entry in repo.fetch_user_history("alice")] == [
        ("openrecipes:2", False), ("openrecipes:1", True),
    ]


def test_rating_an_unknown_recipe_or_user_is_404(repo):
    alice = repo.create_user("alice")

    with TestClient(app) as client:
        recipe = client.put("/api/recipes/openrecipes:999/rating", params={"user_id": alice.id, "like": True})
        user = client.put("/api/recipes/openrecipes:1/rating", params={"user_id": 999, "like": True})
        invalid = client.put("/api/recipes/openrecipes:1/rating", params={"user_id": alice.id, "like": "maybe"})

    assert (recipe.status_code, recipe.json()) == (404, {"detail": "Recipe not found."})
    assert (user.status_code, user.json()) == (404, {"detail": "User not found."})
    assert invalid.status_code == 422
    assert repo.fetch_user_history("alice") == []


def test_startup_creates_the_database_and_cache(users, cache):
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.app.state.cache is cache


def test_a_user_is_looked_up_by_username(repo):
    alice = repo.create_user("alice", Preferences(special_diet="halal", cuisines=["Thai"]))
    bob = repo.create_user("bob", Preferences(budget=12.5))

    with TestClient(app) as client:
        found = client.get("/api/users/alice")
        with_budget = client.get("/api/users/bob")
        missing = client.get("/api/users/nobody")

    assert found.status_code == 200
    # a user without a budget has 0, no limit
    assert found.json() == {
        "id": alice.id, "username": "alice",
        "preferences": {"special_diet": "halal", "cuisines": ["Thai"], "preferred_nutrient": None, "budget": 0.0,
                        "allergies": []},
    }
    assert with_budget.json() == {
        "id": bob.id, "username": "bob",
        "preferences": {"special_diet": None, "cuisines": [], "preferred_nutrient": None, "budget": 12.5,
                        "allergies": []},
    }
    assert (missing.status_code, missing.json()) == (404, {"detail": "User not found."})


class OneFoodCatalog:
    """Stands in for the catalog's RecipeRepository: its only food is fdc_id 169286."""

    def food(self, fdc_id: int) -> object | None:
        return object() if fdc_id == 169286 else None

    def foods_by_ids(self, fdc_ids) -> list:
        return [SimpleNamespace(fdc_id=169286, description="Spinach, raw") for fdc_id in fdc_ids if fdc_id == 169286]


def test_rating_again_changes_the_like_and_dates_it_now(users, repo):
    alice = repo.create_user("alice")
    repo.add_history(alice.id, "openrecipes:1", like=True)
    with users:
        users.execute("UPDATE user_history SET timestamp = '2026-01-01T00:00:00.000Z'")

    again = repo.add_history(alice.id, "openrecipes:1", like=False)

    assert repo.fetch_user_history("alice") == [again]
    assert again.like is False
    assert again.timestamp > datetime(2026, 1, 2, tzinfo=timezone.utc)


def test_a_catalog_food_is_rated_by_its_fdc_id(repo):
    alice = repo.create_user("alice")

    assert rate_item(repo, OneFoodCatalog(), alice.id, "169286", like=True).recipe_id == "169286"
    with pytest.raises(LookupError):
        rate_item(repo, OneFoodCatalog(), alice.id, "999", like=True)
    # without a catalog no food is known
    with pytest.raises(LookupError):
        rate_item(repo, None, alice.id, "169286", like=False)


def test_the_likes_route_gives_each_rated_ids_latest_like(users, repo):
    alice = repo.create_user("alice")
    repo.create_user("bob")
    repo.add_history(alice.id, "openrecipes:1", like=True)
    repo.add_history(alice.id, "169286", like=False)
    # a second row for a recipe, as an older database can hold: the later one counts
    with users:
        users.execute("INSERT INTO user_history (user_id, timestamp, liked, recipe_id) "
                      "VALUES (?, '2000-01-01T00:00:00.000Z', 0, 'openrecipes:1')", (alice.id,))

    with TestClient(app) as client:
        liked = client.get("/api/users/alice/likes")
        none = client.get("/api/users/bob/likes")
        missing = client.get("/api/users/nobody/likes")

    assert liked.json() == {"openrecipes:1": True, "169286": False}
    assert none.json() == {}
    assert (missing.status_code, missing.json()) == (404, {"detail": "User not found."})


def test_the_history_route_lists_ratings_newest_first_with_titles(users, repo, override):
    alice = repo.create_user("alice")
    repo.create_user("bob")
    repo.add_history(alice.id, "openrecipes:1", like=True)
    repo.add_history(alice.id, "169286", like=False)
    # a recipe the recipe database no longer has
    with users:
        users.execute("INSERT INTO user_history (user_id, timestamp, liked, recipe_id) "
                      "VALUES (?, '2000-01-01T00:00:00.000Z', 1, 'openrecipes:404')", (alice.id,))
    override(get_recipe_repository, OneFoodCatalog)

    with TestClient(app) as client:
        history = client.get("/api/users/alice/history").json()
        newest = client.get("/api/users/alice/history", params={"limit": 1}).json()
        empty = client.get("/api/users/bob/history").json()
        missing = client.get("/api/users/nobody/history")

    assert [(item["recipe_id"], item["kind"], item["title"], item["like"]) for item in history] == [
        ("169286", "food", "Spinach, raw", False),
        ("openrecipes:1", "recipe", "Tomato Soup", True),
        ("openrecipes:404", "recipe", None, True),
    ]
    assert [item["recipe_id"] for item in newest] == ["169286"]
    assert empty == []
    assert (missing.status_code, missing.json()) == (404, {"detail": "User not found."})


def test_the_preferences_route_replaces_them_and_drops_cached_recommendations(repo, cache):
    alice = repo.create_user("alice", Preferences(special_diet="halal", cuisines=["Thai"]))
    cache.store[f"recommendations:{alice.id}"] = b"{}"
    wanted = {"special_diet": "vegetarian", "cuisines": ["Italian"], "preferred_nutrient": "protein", "budget": 20.0,
              "allergies": ["peanut", "shellfish"]}

    with TestClient(app) as client:
        updated = client.put("/api/users/alice/set-preferences", json=wanted)
        found = client.get("/api/users/alice").json()
        invalid = client.put("/api/users/alice/set-preferences", json={**wanted, "special_diet": "vegan"})
        unknown_allergen = client.put("/api/users/alice/set-preferences", json={**wanted, "allergies": ["nuts"]})
        missing = client.put("/api/users/nobody/set-preferences", json=wanted)

    assert updated.status_code == 200
    assert updated.json() == {"id": alice.id, "username": "alice", "preferences": wanted}
    assert found["preferences"] == wanted
    assert f"recommendations:{alice.id}" not in cache.store
    assert invalid.status_code == 422
    assert unknown_allergen.status_code == 422
    assert (missing.status_code, missing.json()) == (404, {"detail": "User not found."})
