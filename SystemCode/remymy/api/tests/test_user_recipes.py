from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from remymy_api.db import database_path
from remymy_api.main import app
from remymy_api.schema import connect

RECIPE = {
    "user_id": 1,
    "title": "Grandma's chicken curry",
    "ingredients": ["500 g chicken thighs", "1 onion, chopped", "2 tbsp curry paste"],
    "instructions": ["Brown the chicken.", "Add the onion and paste.", "Simmer for 30 minutes."],
    "tags": [{"label": "hearty_filling", "source": "classifier"}, {"label": "family_favourite", "source": "user"}],
    "nutrients": {"energy_kcal": 180, "protein_g": 14},
}


def query(sql: str, *parameters: object) -> list[tuple]:
    connection = connect(database_path())
    try:
        return [tuple(row) for row in connection.execute(sql, parameters)]
    finally:
        connection.close()


@pytest.fixture
def client(monkeypatch, tmp_path, cache):
    """The API on a database with the user alice, user_id 1."""
    with TestClient(app) as test_client:
        connection = connect(database_path())
        with connection:
            connection.execute("INSERT INTO app_user (username) VALUES ('alice')")
        connection.close()
        yield test_client


def test_a_new_recipe_is_stored_and_returned_as_stored(client):
    response = client.post("/api/recipes", json=RECIPE)

    assert response.status_code == 201
    stored = response.json()
    assert stored.pop("created_at")
    assert stored == {
        "id": "user-recipe:1", "user_id": 1, "title": "Grandma's chicken curry",
        "ingredients": RECIPE["ingredients"], "instructions": RECIPE["instructions"],
        # in label order, as the ORM reads them back
        "tags": [{"label": "family_favourite", "source": "user"}, {"label": "hearty_filling", "source": "classifier"}],
        "nutrients": {"energy_kcal": 180.0, "protein_g": 14.0},
    }
    # a recipe like the others, in recipe with its lines and tags, and the owner and nutrients in user_recipe
    assert query("SELECT recipe_id, title, kind FROM recipe WHERE kind = 'user'") == [
        ("user-recipe:1", "Grandma's chicken curry", "user")]
    assert query("SELECT raw_text FROM recipe_ingredient WHERE recipe_id = 'user-recipe:1' ORDER BY position") == [
        (line,) for line in RECIPE["ingredients"]]
    assert query("SELECT text FROM recipe_instruction WHERE recipe_id = 'user-recipe:1' ORDER BY position") == [
        (step,) for step in RECIPE["instructions"]]
    assert query("SELECT label, source FROM recipe_tag ORDER BY label") == [
        ("family_favourite", "user"), ("hearty_filling", "classifier")]
    assert query("SELECT recipe_id, user_id, energy_kcal, protein_g, fat_g FROM user_recipe") == [
        ("user-recipe:1", 1, 180.0, 14.0, None)]


def test_a_new_recipe_opens_and_is_listed_like_the_other_recipes(client):
    client.post("/api/recipes", json=RECIPE)

    detail = client.get("/api/recipes/user-recipe:1")
    assert detail.status_code == 200
    assert detail.json() == {
        "id": "user-recipe:1", "title": "Grandma's chicken curry", "ingredients": RECIPE["ingredients"],
        "instructions": RECIPE["instructions"], "quality_status": "accepted",
        "tags": ["family_favourite", "hearty_filling"],
    }
    listed = client.get("/api/recipes", params={"q": "curry"}).json()
    assert [item["id"] for item in listed["items"]] == ["user-recipe:1"]
    assert listed["items"][0]["ingredient_count"] == 3


def test_each_new_recipe_gets_its_own_id_and_can_be_saved(client):
    first = client.post("/api/recipes", json=RECIPE).json()
    second = client.post("/api/recipes", json=RECIPE | {"title": "Quick curry", "tags": [], "nutrients": {}}).json()

    assert (first["id"], second["id"]) == ("user-recipe:1", "user-recipe:2")
    assert (second["tags"], second["nutrients"], second["instructions"]) == ([], {}, RECIPE["instructions"])
    assert client.put(f"/api/users/alice/saved/{second['id']}").status_code == 200


def test_an_unknown_user_is_404_and_nothing_is_stored(client):
    response = client.post("/api/recipes", json=RECIPE | {"user_id": 99})

    assert response.status_code == 404
    assert response.json() == {"detail": "User not found."}
    assert query("SELECT COUNT(*) FROM user_recipe") == [(0,)]


def test_a_users_recipes_come_back_whole_and_newest_first(client):
    client.post("/api/recipes", json=RECIPE)
    client.post("/api/recipes", json=RECIPE | {"title": "Quick curry", "tags": [], "nutrients": {}, "instructions": []})
    connection = connect(database_path())
    with connection:
        connection.execute("INSERT INTO app_user (username) VALUES ('bob')")
    connection.close()
    client.post("/api/recipes", json=RECIPE | {"user_id": 2, "title": "Bob's curry"})

    response = client.get("/api/users/alice/recipes")

    assert response.status_code == 200
    recipes = response.json()
    assert [recipe["id"] for recipe in recipes] == ["user-recipe:2", "user-recipe:1"]
    for recipe in recipes:
        assert recipe.pop("created_at")
    assert recipes == [
        {"id": "user-recipe:2", "user_id": 1, "title": "Quick curry", "ingredients": RECIPE["ingredients"],
         "instructions": [], "tags": [], "nutrients": {}},
        {"id": "user-recipe:1", "user_id": 1, "title": "Grandma's chicken curry", "ingredients": RECIPE["ingredients"],
         "instructions": RECIPE["instructions"],
         "tags": [{"label": "family_favourite", "source": "user"}, {"label": "hearty_filling", "source": "classifier"}],
         "nutrients": {"energy_kcal": 180.0, "protein_g": 14.0}},
    ]
    assert [recipe["title"] for recipe in client.get("/api/users/bob/recipes").json()] == ["Bob's curry"]


def test_a_user_without_recipes_has_none_and_an_unknown_user_is_404(client):
    assert client.get("/api/users/alice/recipes").json() == []
    response = client.get("/api/users/nobody/recipes")
    assert (response.status_code, response.json()) == (404, {"detail": "User not found."})


def test_a_users_recipes_are_read_in_one_query_joining_the_recipe_tables(client):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from remymy_api.db import database_engine
    from remymy_api.repository.recipe_repository import RecipeRepository

    client.post("/api/recipes", json=RECIPE)
    client.post("/api/recipes", json=RECIPE | {"title": "Quick curry"})
    statements: list[str] = []
    engine = database_engine()
    listener = lambda _conn, _cursor, statement, *_: statements.append(statement)  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        with Session(engine) as session:
            recipes = RecipeRepository(session).user_recipes(1)
            # reading every field takes no further query
            read = [(recipe.title, recipe.ingredients, recipe.instructions, [tag.label for tag in recipe.tags])
                    for recipe in recipes]
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert len(read) == 2 and read[1][1] == RECIPE["ingredients"]
    assert len(statements) == 1
    sql = statements[0]
    assert "JOIN recipe " in sql
    for table in ("recipe_ingredient", "recipe_instruction", "recipe_tag"):
        assert f"LEFT OUTER JOIN {table}" in sql


@pytest.mark.parametrize("change", [
    {"title": ""},
    {"ingredients": []},
    {"ingredients": [""]},
    {"tags": [{"label": "Hearty Filling", "source": "user"}]},
    {"tags": [{"label": "easy", "source": "chef"}]},
    {"tags": [{"label": "easy", "source": "user"}, {"label": "easy", "source": "classifier"}]},
    {"nutrients": {"vitamin_c_mg": 2}},
    {"servings": 4},
])
def test_invalid_recipes_are_422(client, change):
    assert client.post("/api/recipes", json=RECIPE | change).status_code == 422


def test_create_recipe_is_in_the_openapi_schema(client):
    operation = client.get("/openapi.json").json()["paths"]["/api/recipes"]["post"]

    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/UserRecipeCreate")
    assert {"201", "404", "503", "422"} <= set(operation["responses"])
