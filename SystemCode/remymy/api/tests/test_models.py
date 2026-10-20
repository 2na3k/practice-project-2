from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from remymy_api import models
from remymy_api.db import get_db, user_database_path, users_engine
from remymy_api.models import AppUser, UserRecipe, UserRecipeTag
from remymy_api.schemas import NutrientName, UserRecipeCreate, UserRecipeResponse
from remymy_api.users import init_user_database

REQUEST = {
    "user_id": 1,
    "title": "Grandma's chicken curry",
    "ingredients": ["500 g chicken thighs", "1 onion, chopped", "2 tbsp curry paste"],
    "instructions": ["Brown the chicken.", "Simmer for 30 minutes."],
    "tags": [{"label": "hearty_filling", "source": "classifier"}, {"label": "family_favourite", "source": "user"}],
    "nutrients": {"energy_kcal": 180, "protein_g": 14},
}


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setenv("USER_DATABASE_PATH", str(tmp_path / "users.db"))
    # the demo user, user_id 1, to own the recipes
    monkeypatch.setenv("SEED_DEMO_USER", "true")
    init_user_database()
    return users_engine()


def recipe_from(body: UserRecipeCreate) -> UserRecipe:
    """The request DTO as an ORM object, as POST /api/recipes would store it."""
    return UserRecipe(
        user_id=body.user_id, title=body.title, ingredients=body.ingredients, instructions=body.instructions,
        nutrients=body.nutrients, tags=[UserRecipeTag(label=tag.label, source=tag.source) for tag in body.tags],
    )


def test_startup_creates_the_orm_tables_and_leaves_the_sql_ones_be(engine):
    tables = set(inspect(engine).get_table_names())

    assert {"app_user", "user_history", "user_recipe", "user_recipe_tag"} <= tables
    # app_user is mapped, but its table is still users.SCHEMA's: the JSONB preferences keep their SQL checks
    with engine.connect() as connection:
        ddl = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name = 'app_user'").scalar_one()
    assert "json_valid(preferences, 8)" in ddl


def test_a_request_dto_is_stored_and_read_back_as_the_response_dto(engine):
    before = datetime.now(UTC) - timedelta(seconds=1)
    with Session(engine) as session:
        session.add(recipe_from(UserRecipeCreate.model_validate(REQUEST)))
        session.commit()

    with Session(engine) as session:
        recipe = session.scalars(select(UserRecipe)).one()
        response = UserRecipeResponse.model_validate(recipe)

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
    # stored in the users database's own time format, like user_history.timestamp
    with sqlite3.connect(user_database_path()) as connection:
        stored = connection.execute("SELECT created_at FROM user_recipe").fetchone()[0]
    assert stored.endswith("Z") and "T" in stored


def test_a_user_has_their_recipes_newest_first(engine):
    with Session(engine) as session:
        demo = session.get(AppUser, 1)
        demo.recipes.append(UserRecipe(title="Toast", ingredients=["bread"]))
        session.flush()
        demo.recipes.append(UserRecipe(title="Tea", ingredients=["tea bag", "water"],
                                       created_at=datetime.now(UTC) + timedelta(minutes=1)))
        session.commit()
        session.expire_all()

        assert [recipe.title for recipe in session.get(AppUser, 1).recipes] == ["Tea", "Toast"]
        assert session.get(AppUser, 2) is None


def test_the_database_keeps_a_recipe_to_a_known_user_and_cascades(engine):
    with Session(engine) as session:
        session.add(recipe_from(UserRecipeCreate.model_validate(REQUEST | {"user_id": 999})))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(recipe_from(UserRecipeCreate.model_validate(REQUEST)))
        session.commit()

    # deleting the user (with SQL, as users.py would) deletes their recipes and the recipes' tags
    with sqlite3.connect(user_database_path()) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("DELETE FROM app_user WHERE user_id = 1")
        assert connection.execute("SELECT count(*) FROM user_recipe").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM user_recipe_tag").fetchone()[0] == 0


@pytest.mark.parametrize("change", [
    {"title": ""},
    {"ingredients": []},
    {"ingredients": "500 g chicken thighs"},
    {"tags": [UserRecipeTag(label="easy", source="chef")]},
])
def test_the_database_checks_what_the_dtos_check(engine, change):
    recipe = UserRecipe(user_id=1, title="Toast", ingredients=["bread"])
    for key, value in change.items():
        setattr(recipe, key, value)
    with Session(engine) as session:
        session.add(recipe)
        with pytest.raises(IntegrityError):
            session.commit()


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
    finally:
        dependency.close()
