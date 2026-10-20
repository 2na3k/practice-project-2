"""SQLAlchemy ORM models: the tables of the users database (USER_DATABASE_PATH) the ORM maps.

The API's request and response models (DTOs) stay in schemas.py. A response DTO with from_attributes reads an ORM
object as it is, e.g. schemas.UserRecipeResponse.model_validate(user_recipe)."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import JSON, CheckConstraint, Dialect, Engine, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .db import Base

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


class AppUser(Base):
    """A user of the app. users.SCHEMA creates its table, with checks and triggers on preferences (SQLite JSONB, read
    and written with SQL in users.py, so it is not mapped here); the ORM maps it so a recipe belongs to its user."""

    __tablename__ = "app_user"
    __table_args__ = {"info": {"created_by": "users.SCHEMA"}}

    user_id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp, server_default=NOW)

    recipes: Mapped[list[UserRecipe]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True,
        order_by=lambda: UserRecipe.created_at.desc(),
    )


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
    """Create the tables only the ORM defines, where missing. The ones users.SCHEMA creates, with checks and triggers
    the ORM does not model, are left to it: it runs first."""
    Base.metadata.create_all(
        engine, tables=[table for table in Base.metadata.sorted_tables if table.info.get("created_by") != "users.SCHEMA"])
