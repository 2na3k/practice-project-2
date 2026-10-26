from __future__ import annotations

import pytest
from conftest import add_recipes
from fastapi.testclient import TestClient
from test_recipe_repository import food, write_catalog

from remymy_api.db import database_path
from remymy_api.main import app
from remymy_api.schema import connect, init_database, merge_food_data


@pytest.fixture
def client(tmp_path, monkeypatch, cache):
    """The API on a database with two Food.com recipes, the catalog food 170138, and the users alice and bob."""
    add_recipes([("openrecipes:1", "Tomato Soup", "accepted"), ("openrecipes:2", "Apple Salad", "accepted")])
    monkeypatch.setenv("RECIPE_PATH", str(write_catalog(tmp_path / "catalog.csv", [food(170138, "Spinach, raw")])))
    with TestClient(app) as test_client:
        connection = connect(database_path())
        with connection:
            connection.execute("INSERT INTO app_user (username) VALUES ('alice'), ('bob')")
        connection.close()
        yield test_client


def query(sql: str, *parameters: object) -> list[tuple]:
    connection = connect(database_path())
    try:
        with connection:
            return [tuple(row) for row in connection.execute(sql, parameters)]
    finally:
        connection.close()


def test_recipes_and_catalog_foods_are_saved_and_listed_newest_first(client):
    recipe = client.put("/api/users/alice/saved/openrecipes:1")
    spinach = client.put("/api/users/alice/saved/170138")
    # saved already: it keeps the time it was saved
    again = client.put("/api/users/alice/saved/openrecipes:1")
    query("UPDATE saved_recipe SET saved_at = '2026-01-01T00:00:00.000Z' WHERE recipe_id = 'openrecipes:1'")

    saved = client.get("/api/users/alice/saved").json()

    assert recipe.status_code == spinach.status_code == again.status_code == 200
    assert again.json() == recipe.json()
    assert spinach.json()["kind"] == "food"
    assert [(item["recipe_id"], item["kind"], item["title"]) for item in saved] == [
        ("170138", "food", "Spinach, raw"), ("openrecipes:1", "recipe", "Tomato Soup"),
    ]
    # each user has their own
    assert client.get("/api/users/bob/saved").json() == []


def test_a_saved_recipe_is_unsaved(client):
    client.put("/api/users/alice/saved/openrecipes:1")

    removed = client.delete("/api/users/alice/saved/openrecipes:1")
    # unsaved already: gone either way
    again = client.delete("/api/users/alice/saved/openrecipes:1")

    assert removed.status_code == again.status_code == 204
    assert client.get("/api/users/alice/saved").json() == []


def test_an_unknown_user_or_recipe_is_404(client):
    unknown_recipe = client.put("/api/users/alice/saved/openrecipes:999")

    assert (unknown_recipe.status_code, unknown_recipe.json()) == (404, {"detail": "Recipe not found."})
    for response in (client.get("/api/users/nobody/saved"), client.put("/api/users/nobody/saved/openrecipes:1"),
                     client.delete("/api/users/nobody/saved/openrecipes:1")):
        assert (response.status_code, response.json()) == (404, {"detail": "User not found."})
    assert query("SELECT count(*) FROM saved_recipe") == [(0,)]


def test_saves_go_with_their_user_or_recipe(client):
    client.put("/api/users/alice/saved/openrecipes:1")
    client.put("/api/users/alice/saved/openrecipes:2")
    client.put("/api/users/bob/saved/openrecipes:2")

    query("DELETE FROM app_user WHERE username = 'alice'")
    assert query("SELECT recipe_id FROM saved_recipe") == [("openrecipes:2",)]
    query("DELETE FROM recipe WHERE recipe_id = 'openrecipes:2'")
    assert query("SELECT count(*) FROM saved_recipe") == [(0,)]


def test_merging_the_food_data_again_keeps_the_saves(client, tmp_path):
    client.put("/api/users/alice/saved/openrecipes:1")
    # a newer ingest: openrecipes:1 retitled, openrecipes:3 new, openrecipes:2 not in it
    newer = add_recipes([("openrecipes:1", "Tomato Soup, renamed", "accepted"), ("openrecipes:3", "Pea Soup", "accepted")],
                        path=tmp_path / "newer.db")

    connection = connect(database_path())
    try:
        merge_food_data(connection, newer)
    finally:
        connection.close()

    assert query("SELECT recipe_id, title FROM recipe WHERE kind = 'openrecipes' ORDER BY 1") == [
        ("openrecipes:1", "Tomato Soup, renamed"), ("openrecipes:2", "Apple Salad"), ("openrecipes:3", "Pea Soup"),
    ]
    assert [item["title"] for item in client.get("/api/users/alice/saved").json()] == ["Tomato Soup, renamed"]


def test_startup_takes_the_old_recipe_database_once(tmp_path, monkeypatch):
    legacy = add_recipes([("openrecipes:7", "Lentil Stew", "accepted")], path=tmp_path / "remymy-food.db")
    monkeypatch.setenv("LEGACY_FOOD_DATABASE_PATH", str(legacy))

    init_database()
    add_recipes([("openrecipes:8", "Bean Stew", "accepted")], path=legacy)
    init_database()

    # the database had its recipes the second time, so the old one was not read again
    assert query("SELECT recipe_id FROM recipe WHERE kind = 'openrecipes'") == [("openrecipes:7",)]


def test_the_migration_job_loads_the_old_recipe_database_into_remymy_db(tmp_path, monkeypatch):
    from migrations import bootstrap

    target = tmp_path / "state" / "remymy.db"
    legacy = add_recipes([("openrecipes:7", "Lentil Stew", "accepted")], path=tmp_path / "remymy-food.db")
    # remymy.db as an earlier start of the API left it: its tables, without recipes
    init_database()

    assert bootstrap.main(["--database-path", str(target), "--legacy-database", str(tmp_path / "none.db"),
                           "--validate-only"]) == 1
    assert bootstrap.main(["--database-path", str(target), "--legacy-database", str(tmp_path / "none.db")]) == 1
    assert bootstrap.main(["--database-path", str(target), "--legacy-database", str(legacy)]) == 0
    # loaded already: accepted as it is
    assert bootstrap.main(["--database-path", str(target), "--legacy-database", str(tmp_path / "none.db")]) == 0
    assert query("SELECT recipe_id, title FROM recipe") == [("openrecipes:7", "Lentil Stew")]
