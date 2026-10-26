from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from remymy_api import models
from remymy_api.db import database_engine, database_path, get_db
from remymy_api.models import AppUser, RecipeEntry, RecipeIngredientEntry, RecipeTagEntry, UserRecipe
from remymy_api.repository.recipe_repository import RecipeRepository
from remymy_api.schema import connect, init_database
from remymy_api.schemas import NutrientName, UserRecipeCreate, UserRecipeResponse

REQUEST = {
    "user_id": 1,
    "title": "Grandma's chicken curry",
    "ingredients": ["500 g chicken thighs", "1 onion, chopped", "2 tbsp curry paste"],
    "instructions": ["Brown the chicken.", "Simmer for 30 minutes."],
    "tags": [{"label": "hearty_filling", "source": "classifier"}, {"label": "family_favourite", "source": "user"}],
    "nutrients": {"energy_kcal": 180, "protein_g": 14},
}


@pytest.fixture
def engine(monkeypatch):
    # the demo user, user_id 1, to own the recipes
    monkeypatch.setenv("SEED_DEMO_USER", "true")
    init_database()
    return database_engine()


def store(engine, request: dict = REQUEST) -> str:
    """POST /api/recipes's write; the new recipe's id."""
    with Session(engine) as session:
        return RecipeRepository(session).create_user_recipe(UserRecipeCreate.model_validate(request)).recipe_id


def user_recipe(recipe_id: str, title: str, lines: list[str], **values: object) -> UserRecipe:
    """A user recipe as ORM objects, its lines and title in its row of recipe."""
    return UserRecipe(recipe_id=recipe_id, recipe=RecipeEntry(
        recipe_id=recipe_id, title=title, quality_status="accepted", kind="user",
        ingredients=[RecipeIngredientEntry(ingredient_id=models.ingredient_id(recipe_id, position), position=position,
                                           raw_text=line) for position, line in enumerate(lines, start=1)],
    ), **values)


def test_startup_creates_the_orm_tables_and_leaves_the_sql_ones_be(engine):
    tables = set(inspect(engine).get_table_names())

    assert {"app_user", "user_history", "saved_recipe", "recipe", "recipe_ingredient", "recipe_instruction",
            "user_recipe", "recipe_tag", "price_mapped_nutrients"} <= tables
    assert "user_recipe_tag" not in tables
    # app_user is mapped, but its table is still users.SCHEMA's: the JSONB preferences keep their SQL checks
    with engine.connect() as connection:
        ddl = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name = 'app_user'").scalar_one()
    assert "json_valid(preferences, 8)" in ddl


def test_a_request_dto_is_stored_as_a_recipe_and_read_back_as_the_response_dto(engine):
    before = datetime.now(UTC) - timedelta(seconds=1)
    recipe_id = store(engine)

    with Session(engine) as session:
        response = UserRecipeResponse.model_validate(session.get(UserRecipe, recipe_id))

    assert response.model_dump(exclude={"created_at"}) == {
        "id": "user-recipe:1",
        "user_id": 1,
        "title": "Grandma's chicken curry",
        "ingredients": REQUEST["ingredients"],
        "instructions": REQUEST["instructions"],
        # by label
        "tags": [{"label": "family_favourite", "source": "user"}, {"label": "hearty_filling", "source": "classifier"}],
        "nutrients": {"protein_g": 14.0, "energy_kcal": 180.0},
    }
    assert before <= response.created_at <= datetime.now(UTC)
    with sqlite3.connect(database_path()) as connection:
        # stored in the users database's own time format, like user_history.timestamp
        stored = connection.execute("SELECT created_at FROM user_recipe").fetchone()[0]
        assert stored.endswith("Z") and "T" in stored
        # the recipe itself is stored as the recipe database's are
        assert connection.execute("SELECT recipe_id, title, kind FROM recipe").fetchall() == [
            ("user-recipe:1", "Grandma's chicken curry", "user")]
        assert connection.execute(
            "SELECT ingredient_id, position, raw_text, parse_status FROM recipe_ingredient ORDER BY position"
        ).fetchall() == [("user-recipe:1:ingredient:1", 1, "500 g chicken thighs", "unparsed"),
                         ("user-recipe:1:ingredient:2", 2, "1 onion, chopped", "unparsed"),
                         ("user-recipe:1:ingredient:3", 3, "2 tbsp curry paste", "unparsed")]
        assert connection.execute("SELECT position, text FROM recipe_instruction ORDER BY position").fetchall() == [
            (1, "Brown the chicken."), (2, "Simmer for 30 minutes.")]
        assert connection.execute("SELECT label, source FROM recipe_tag ORDER BY label").fetchall() == [
            ("family_favourite", "user"), ("hearty_filling", "classifier")]


def test_each_new_recipe_takes_the_next_id(engine):
    assert [store(engine), store(engine), store(engine)] == ["user-recipe:1", "user-recipe:2", "user-recipe:3"]


def test_a_user_has_their_recipes_newest_first(engine):
    with Session(engine) as session:
        demo = session.get(AppUser, 1)
        demo.recipes.append(user_recipe("user-recipe:1", "Toast", ["bread"]))
        session.flush()
        demo.recipes.append(user_recipe("user-recipe:2", "Tea", ["tea bag", "water"],
                                        created_at=datetime.now(UTC) + timedelta(minutes=1)))
        session.commit()
        session.expire_all()

        assert [recipe.title for recipe in session.get(AppUser, 1).recipes] == ["Tea", "Toast"]
        assert session.get(AppUser, 2) is None


def test_the_database_keeps_a_recipe_to_a_known_user_and_cascades(engine):
    with Session(engine) as session, pytest.raises(IntegrityError):
        RecipeRepository(session).create_user_recipe(UserRecipeCreate.model_validate(REQUEST | {"user_id": 999}))
    store(engine)

    # deleting the user (with SQL, as users.py would) deletes their recipes with the recipes' rows in recipe, their
    # lines and tags, and the saves of them
    connection = connect(database_path())
    with connection:
        connection.execute("INSERT INTO saved_recipe (user_id, recipe_id) VALUES (1, 'user-recipe:1')")
        connection.execute("DELETE FROM app_user WHERE user_id = 1")
    for table in ("user_recipe", "recipe_tag", "recipe_ingredient", "recipe_instruction", "saved_recipe"):
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    assert connection.execute("SELECT count(*) FROM recipe WHERE kind = 'user'").fetchone()[0] == 0
    connection.close()


@pytest.mark.parametrize("tag", [
    RecipeTagEntry(label="easy", source="chef"),
    RecipeTagEntry(label="x" * 41, source="user"),
])
def test_the_database_checks_what_the_dtos_check(engine, tag):
    recipe = user_recipe("user-recipe:1", "Toast", ["bread"], user_id=1)
    recipe.recipe.tags.append(tag)
    with Session(engine) as session:
        session.add(recipe)
        with pytest.raises(IntegrityError):
            session.commit()


def test_startup_moves_user_recipes_of_the_old_layout_to_the_recipe_tables(engine):
    # user_recipe as it was: the recipe whole in it, its tags in user_recipe_tag, a trigger copying it to recipe
    connection = connect(database_path())
    with connection:
        connection.executescript("""
            DROP TABLE recipe_tag; DROP TABLE user_recipe;
            CREATE TABLE user_recipe (user_recipe_id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES app_user(user_id),
              title TEXT, ingredients JSON, instructions JSON DEFAULT '[]', protein_g FLOAT, fat_g FLOAT, carb_g FLOAT,
              energy_kcal FLOAT, fiber_g FLOAT, sodium_mg FLOAT, cholesterol_mg FLOAT, satfat_g FLOAT, sugars_g FLOAT,
              created_at TEXT);
            CREATE INDEX user_recipe_by_user ON user_recipe (user_id, created_at);
            CREATE TABLE user_recipe_tag (user_recipe_id INTEGER REFERENCES user_recipe(user_recipe_id), label TEXT,
              source TEXT, PRIMARY KEY (user_recipe_id, label));
            CREATE TRIGGER user_recipe_is_a_recipe AFTER INSERT ON user_recipe BEGIN
              INSERT INTO recipe (recipe_id, title, quality_status, kind)
              VALUES ('user-recipe:' || NEW.user_recipe_id, NEW.title, 'accepted', 'user');
            END;
        """)
        connection.execute("INSERT INTO user_recipe VALUES (7, 1, 'Toast', ?, ?, NULL, NULL, NULL, 90.0, NULL, NULL, "
                           "NULL, NULL, NULL, '2026-10-01T08:00:00.000Z')",
                           (json.dumps(["bread", "butter"]), json.dumps(["Toast the bread."])))
        connection.execute("INSERT INTO user_recipe_tag VALUES (7, 'breakfast', 'classifier')")
        connection.execute("INSERT INTO saved_recipe (user_id, recipe_id) VALUES (1, 'user-recipe:7')")
    connection.close()

    init_database()

    with Session(engine) as session:
        moved = UserRecipeResponse.model_validate(session.get(UserRecipe, "user-recipe:7"))
        # a new recipe takes the next id after the moved one
        assert RecipeRepository(session).create_user_recipe(
            UserRecipeCreate.model_validate(REQUEST)).recipe_id == "user-recipe:8"
    assert moved.model_dump(exclude={"created_at"}) == {
        "id": "user-recipe:7", "user_id": 1, "title": "Toast", "ingredients": ["bread", "butter"],
        "instructions": ["Toast the bread."], "tags": [{"label": "breakfast", "source": "classifier"}],
        "nutrients": {"energy_kcal": 90.0},
    }
    connection = connect(database_path())
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    # the save of it still holds
    assert [row[0] for row in connection.execute("SELECT recipe_id FROM saved_recipe")] == ["user-recipe:7"]
    connection.close()
    assert not tables & {"old_user_recipe", "old_user_recipe_tag", "user_recipe_tag"}
    assert "user_recipe_is_a_recipe" not in triggers and "user_recipe_deleted_takes_its_recipe" in triggers


def test_nutrients_are_the_columns_the_dtos_name():
    assert set(models.NUTRIENT_COLUMNS) == set(get_args(NutrientName))
    assert set(models.NUTRIENT_COLUMNS) <= set(UserRecipe.__table__.columns.keys())
    with pytest.raises(ValueError, match="vitamin_c_mg"):
        UserRecipe(nutrients={"vitamin_c_mg": 2.0})


def test_get_db_opens_a_session_on_the_users_database(engine):
    dependency = get_db()
    session = next(dependency)
    try:
        assert session.get(AppUser, 1).username == "demo"
        assert session.scalars(select(UserRecipe)).all() == []
    finally:
        dependency.close()
