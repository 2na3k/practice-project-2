from __future__ import annotations

import sqlite3

import pytest
from conftest import add_recipes
from fastapi import HTTPException
from fastapi.testclient import TestClient

from remymy_api.db import close_database, database_path, open_database
from remymy_api.main import app
from remymy_api.schema import connect


@pytest.fixture
def client():
    add_recipes(
        [
            ("openrecipes:1", "Tomato Soup", "accepted"),
            ("openrecipes:2", "Apple Salad", "partial"),
            ("openrecipes:3", "Long Ingredient List", "accepted"),
            ("openrecipes:4", None, "quarantined"),
        ],
        [
            ("openrecipes:1", 1, "2 cups tomatoes", "tomatoes"),
            ("openrecipes:1", 2, "1 tsp salt", "salt"),
            ("openrecipes:2", 1, "1 apple", "apple"),
            ("openrecipes:3", 1, "one", "one"),
            ("openrecipes:3", 2, "two", "two"),
            ("openrecipes:3", 3, "three", "three"),
            ("openrecipes:3", 4, "four", "four"),
        ],
        [
            ("openrecipes:1", 1, "Blend tomatoes."),
            ("openrecipes:1", 2, "Season and serve."),
        ],
    )
    # a catalog food's row, as startup adds one for each: not a Food.com recipe, so the recipe routes leave it out
    connection = connect(database_path())
    with connection:
        connection.execute("INSERT INTO recipe (recipe_id, title, quality_status, kind) "
                           "VALUES ('170138', 'Tomatoes, red, ripe, canned', 'accepted', 'catalog')")
    connection.close()
    return TestClient(app)


def test_health_is_process_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_requires_a_usable_recipe_database(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_returns_only_the_narrow_summary_contract(client):
    response = client.get("/api/recipes", params={"q": "tomato"})
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "openrecipes:1",
                "title": "Tomato Soup",
                "ingredient_count": 2,
                "ingredient_preview": ["2 cups tomatoes", "1 tsp salt"],
                "quality_status": "accepted",
            }
        ],
        "total": 1,
        "limit": 20,
        "offset": 0,
    }


def test_detail_returns_ordered_raw_recipe_text(client):
    response = client.get("/api/recipes/openrecipes:1")
    assert response.status_code == 200
    assert response.json() == {
        "id": "openrecipes:1",
        "title": "Tomato Soup",
        "ingredients": ["2 cups tomatoes", "1 tsp salt"],
        "instructions": ["Blend tomatoes.", "Season and serve."],
        "quality_status": "accepted",
    }


def test_summary_excludes_blank_titles_and_caps_preview(client):
    response = client.get("/api/recipes/", params={"q": "Long Ingredient"})
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["ingredient_count"] == 4
    assert item["ingredient_preview"] == ["one", "two", "three"]

    assert client.get("/api/recipes/openrecipes:4").status_code == 404


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"limit": 0}, "Input should be greater than or equal to 1"),
        ({"limit": 101}, "Input should be less than or equal to 100"),
        ({"offset": -1}, "Input should be greater than or equal to 0"),
    ],
)
def test_pagination_bounds_are_validated(client, params, message):
    response = client.get("/api/recipes", params=params)
    assert response.status_code == 422
    assert message in response.text


def test_missing_database_is_non_leaky_503(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "missing.db"))
    client = TestClient(app)
    response = client.get("/api/recipes")
    assert response.status_code == 503
    assert response.json() == {"detail": "Recipe data is unavailable."}
    assert str(tmp_path) not in response.text

    ready_response = client.get("/ready")
    assert ready_response.status_code == 503
    assert ready_response.json() == {"detail": "Recipe data is unavailable."}


def test_zero_byte_database_is_not_ready(monkeypatch, tmp_path):
    database = tmp_path / "empty.db"
    database.touch()
    monkeypatch.setenv("DATABASE_PATH", str(database))

    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Recipe data is unavailable."}


def test_unknown_recipe_is_404(client):
    response = client.get("/api/recipes/not-there")
    assert response.status_code == 404
    assert response.json() == {"detail": "Recipe not found."}


def test_a_catalog_foods_recipe_row_is_not_a_listed_recipe(client):
    assert client.get("/api/recipes/170138").status_code == 404
    assert client.get("/api/recipes", params={"q": "canned"}).json()["total"] == 0


def test_a_database_without_recipes_is_not_ready(database):
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Recipe data is unavailable."}


def test_opener_reads_a_wal_mode_database_and_never_creates_one(tmp_path, monkeypatch):
    database = tmp_path / "wal recipes.db"
    add_recipes([("openrecipes:wal", "WAL Recipe", "accepted")], path=database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    connection.close()

    monkeypatch.setenv("DATABASE_PATH", str(database))
    opened = open_database()
    try:
        assert opened.execute("SELECT title FROM recipe").fetchone()[0] == "WAL Recipe"
    finally:
        close_database(opened)

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "missing.db"))
    with pytest.raises(HTTPException):
        open_database()
    assert not (tmp_path / "missing.db").exists()
