"""SQLAlchemy ORM models of the two databases the API writes: the users database (USER_DATABASE_PATH, on Base) and the
catalog database (CATALOG_DATABASE_PATH, on CatalogBase). The recipe database is still read with SQL (db.py).

The API's request and response models (DTOs) stay in schemas.py. A response DTO with from_attributes reads an ORM
object as it is, e.g. schemas.UserRecipeResponse.model_validate(user_recipe)."""
from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, Dialect, Engine, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.elements import BindParameter, ColumnElement
from sqlalchemy.types import TypeDecorator

from .db import Base, CatalogBase

# The time a row is written, in the users database's format (as users.SCHEMA writes it): ISO 8601 UTC with a Z.
NOW = text("(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))")

# The nutrients per 100 g a user may give their recipe: the names of schemas.NutrientName.
NUTRIENT_COLUMNS = ("protein_g", "fat_g", "carb_g", "energy_kcal", "fiber_g", "sodium_mg", "cholesterol_mg",
                    "satfat_g", "sugars_g")

# The prefix of a user's own recipe's id among the app's recipes, so it cannot clash with the recipe database's
# "openrecipes:<n>" or a catalog food's fdc_id (which is all digits, see is_food_id).
RECIPE_ID_PREFIX = "user-recipe:"


class UtcTimestamp(TypeDecorator[datetime]):
    """A UTC time stored as ISO 8601 text, the users database's format, read back as an aware datetime."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return f"{value:%Y-%m-%dT%H:%M:%S}.{value.microsecond // 1000:03d}Z"

    def process_result_value(self, value: str | None, dialect: Dialect) -> datetime | None:
        return None if value is None else datetime.fromisoformat(value)


class SQLiteJSONB(TypeDecorator[Any]):
    """A JSON value stored as SQLite's binary JSONB, as app_user.preferences is: written through jsonb() and read
    through json(), so Python sees the plain value."""

    impl = Text
    cache_ok = True

    def bind_expression(self, bindvalue: BindParameter[Any]) -> ColumnElement[Any]:
        return func.jsonb(bindvalue, type_=self)

    def column_expression(self, column: ColumnElement[Any]) -> ColumnElement[Any]:
        return func.json(column, type_=self)

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else json.dumps(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> Any:
        return None if value is None else json.loads(value)


class PythonLiteral(TypeDecorator[Any]):
    """A list or dict stored as the text of its Python literal, as price_mapped_nutrients.csv writes them ("['apple']",
    "{'apple': 0.45}"), so the catalog database holds the CSV's text."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else repr(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> Any:
        return None if value is None else ast.literal_eval(value)


# --- users database (users.SCHEMA creates app_user and user_history; create_tables the rest) ---


class AppUser(Base):
    """A user of the app. users.SCHEMA creates its table, with the checks and triggers that keep preferences to the
    shape of schemas.Preferences."""

    __tablename__ = "app_user"
    __table_args__ = {"info": {"created_by": "users.SCHEMA"}}

    user_id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True)
    # schemas.Preferences as a dict
    preferences: Mapped[dict[str, Any]] = mapped_column(SQLiteJSONB)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, server_default=NOW)

    history: Mapped[list[UserHistoryEntry]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True,
    )
    recipes: Mapped[list[UserRecipe]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True,
        order_by=lambda: UserRecipe.created_at.desc(),
    )


class UserHistoryEntry(Base):
    """One rating in a user's history: whether they liked a recipe of the recipe database ("openrecipes:<n>") or a
    catalog food (its fdc_id), and when they last said so. users.SCHEMA creates the table."""

    __tablename__ = "user_history"
    __table_args__ = {"info": {"created_by": "users.SCHEMA"}}
    # read timestamp, a server default, back in the INSERT ... RETURNING
    __mapper_args__ = {"eager_defaults": True}

    history_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.user_id", ondelete="CASCADE"))
    timestamp: Mapped[datetime] = mapped_column(UtcTimestamp, server_default=NOW)
    # schemas.UserHistory.like, stored as liked: 1 liked, 0 not
    like: Mapped[bool] = mapped_column("liked", Boolean)
    recipe_id: Mapped[str] = mapped_column(Text)

    user: Mapped[AppUser] = relationship(back_populates="history")


class UserRecipe(Base):
    """A recipe a user wrote themselves (POST /api/recipes), with the nutrients per 100 g they gave it."""

    __tablename__ = "user_recipe"
    __table_args__ = (
        CheckConstraint("length(title) BETWEEN 1 AND 200", name="title_length"),
        CheckConstraint("json_type(ingredients) = 'array' AND json_array_length(ingredients) BETWEEN 1 AND 100",
                        name="ingredients_are_lines"),
        CheckConstraint("json_type(instructions) = 'array' AND json_array_length(instructions) <= 100",
                        name="instructions_are_lines"),
        # a user's recipes, newest first
        Index("user_recipe_by_user", "user_id", "created_at"),
    )
    # read created_at, a server default, back in the INSERT ... RETURNING
    __mapper_args__ = {"eager_defaults": True}

    user_recipe_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.user_id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text)
    # one line each, in order. JSON arrays of text, like app_user.preferences: they are only ever read whole.
    ingredients: Mapped[list[str]] = mapped_column(JSON)
    instructions: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    # nutrients per 100 g, a column each as in the catalog; None = not given
    protein_g: Mapped[float | None]
    fat_g: Mapped[float | None]
    carb_g: Mapped[float | None]
    energy_kcal: Mapped[float | None]
    fiber_g: Mapped[float | None]
    sodium_mg: Mapped[float | None]
    cholesterol_mg: Mapped[float | None]
    satfat_g: Mapped[float | None]
    sugars_g: Mapped[float | None]
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, server_default=NOW)

    user: Mapped[AppUser] = relationship(back_populates="recipes")
    tags: Mapped[list[UserRecipeTag]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", passive_deletes=True,
        order_by=lambda: UserRecipeTag.label,
    )

    @property
    def recipe_id(self) -> str:
        """Its id among the app's recipes, e.g. "user-recipe:12" (once flushed)."""
        return f"{RECIPE_ID_PREFIX}{self.user_recipe_id}"

    @property
    def nutrients(self) -> dict[str, float]:
        """The nutrients per 100 g the user gave, by name."""
        return {name: value for name in NUTRIENT_COLUMNS if (value := getattr(self, name)) is not None}

    @nutrients.setter
    def nutrients(self, values: Mapping[str, float]) -> None:
        unknown = set(values) - set(NUTRIENT_COLUMNS)
        if unknown:
            raise ValueError(f"Unknown nutrients: {', '.join(sorted(unknown))}")
        for name in NUTRIENT_COLUMNS:
            setattr(self, name, values.get(name))


class UserRecipeTag(Base):
    """A tag on a user's recipe: one the classifier suggested and the user kept, or one the user added."""

    __tablename__ = "user_recipe_tag"
    __table_args__ = (
        CheckConstraint("length(label) BETWEEN 1 AND 40", name="label_length"),
        CheckConstraint("source IN ('classifier', 'user')", name="source_known"),
        # recipes by tag
        Index("user_recipe_tag_by_label", "label"),
    )

    user_recipe_id: Mapped[int] = mapped_column(
        ForeignKey("user_recipe.user_recipe_id", ondelete="CASCADE"), primary_key=True)
    label: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text)

    recipe: Mapped[UserRecipe] = relationship(back_populates="tags")


def create_tables(engine: Engine) -> None:
    """Create the users database's tables only the ORM defines, where missing. The ones users.SCHEMA creates, with
    checks and triggers the ORM does not model, are left to it: it runs first."""
    Base.metadata.create_all(
        engine, tables=[table for table in Base.metadata.sorted_tables if table.info.get("created_by") != "users.SCHEMA"])


# --- catalog database (created by RecipeRepository.from_csv_to_db) ---


class CatalogFoodRecord(CatalogBase):
    """A priced food of the catalog, price_mapped_nutrients.csv column for column: prices in SGD, nutrients per 100 g
    (None where FDC has no value). Its response model is schemas.CatalogFood."""

    __tablename__ = "price_mapped_nutrients"
    __table_args__ = (
        CheckConstraint("priced_items >= 1", name="priced_items_positive"),
        CheckConstraint("total_items >= priced_items", name="total_items_cover_priced"),
        CheckConstraint("priced_share > 0 AND priced_share <= 1", name="priced_share_range"),
        CheckConstraint("items_without_unit_price BETWEEN 0 AND priced_items", name="unit_prices_missing_range"),
        CheckConstraint("total_price >= 0", name="total_price_positive"),
        CheckConstraint("basket_per_100 >= 0", name="basket_per_100_positive"),
    )

    fdc_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    description: Mapped[str] = mapped_column(Text)
    # the food's ingredient spans
    product: Mapped[list[str]] = mapped_column(PythonLiteral)
    # span -> {'price': pack price, 'quantity': pack size(s) in g / ml}
    item_prices: Mapped[dict[str, dict[str, Any]]] = mapped_column(PythonLiteral)
    # span -> price per 100 g / 100 ml
    item_unit_prices: Mapped[dict[str, float]] = mapped_column(PythonLiteral)
    # span -> 'variation' | 'average' | 'term crawl'
    item_routes: Mapped[dict[str, str]] = mapped_column(PythonLiteral)
    # span -> the brand / variant words it matched
    matched_variations: Mapped[dict[str, str]] = mapped_column(PythonLiteral)
    # spans no listing priced
    unpriced_items: Mapped[list[str]] = mapped_column(PythonLiteral)
    priced_items: Mapped[int]
    total_items: Mapped[int]
    priced_share: Mapped[float]
    items_without_unit_price: Mapped[int]
    adj: Mapped[list[str]] = mapped_column(PythonLiteral)
    verb: Mapped[list[str]] = mapped_column(PythonLiteral)
    # 0 unless every item is priced
    total_price: Mapped[float]
    basket_per_100: Mapped[float]
    protein_g: Mapped[float | None]
    fat_g: Mapped[float | None]
    carb_g: Mapped[float | None]
    energy_kcal: Mapped[float | None]
    fiber_g: Mapped[float | None]
    sodium_mg: Mapped[float | None]
    cholesterol_mg: Mapped[float | None]
    satfat_g: Mapped[float | None]
    sugars_g: Mapped[float | None]
