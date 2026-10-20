"""The users database's schema, migration and demo user, and what the user routes need beyond UserRepository: whether
a rated id is a recipe or a catalog food, and the titles of the ids a history holds. Reading and writing users and
their history is repository.user_repository.UserRepository's, through the ORM."""
from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterable

from fastapi import HTTPException

from .db import close_database, open_database, user_database_path, users_engine
from .models import create_tables
from .repository.recipe_repository import RecipeRepository, is_food_id
from .repository.user_repository import UserRepository
from .schemas import UserHistory

logger = logging.getLogger(__name__)

# The tables created with SQL here, for their JSON checks and triggers; the ORM maps them (models.AppUser,
# models.UserHistoryEntry). The tables only the ORM defines are created after them, by create_tables.
SCHEMA = """
CREATE TABLE IF NOT EXISTS app_user (
  user_id     INTEGER PRIMARY KEY,
  username    TEXT NOT NULL UNIQUE CHECK (length(username) > 0),
  preferences JSONB NOT NULL DEFAULT (
    jsonb('{"special_diet": null, "cuisines": [], "preferred_nutrient": null}')
  ),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

  CHECK (json_valid(preferences, 8)),
  CHECK (
    json_type(preferences) = 'object'
    AND json_type(preferences, '$.special_diet') IS NOT NULL
    AND json_type(preferences, '$.cuisines') IS NOT NULL
    AND json_type(preferences, '$.preferred_nutrient') IS NOT NULL
    AND json_remove(preferences, '$.special_diet', '$.cuisines', '$.preferred_nutrient') = '{}'
  ),
  CHECK (
    json_type(preferences, '$.special_diet') = 'null'
    OR json_extract(preferences, '$.special_diet') IN ('vegetarian', 'halal', 'Hinduism')
  ),
  CHECK (
    json_type(preferences, '$.cuisines') = 'array'
    AND json_array_length(preferences, '$.cuisines') <= 40
  ),
  CHECK (
    json_type(preferences, '$.preferred_nutrient') = 'null'
    OR (
      json_type(preferences, '$.preferred_nutrient') = 'text'
      AND length(json_extract(preferences, '$.preferred_nutrient')) <= 64
    )
  )
);

CREATE TRIGGER IF NOT EXISTS app_user_cuisines_are_text_on_insert
BEFORE INSERT ON app_user
WHEN EXISTS (SELECT 1 FROM json_each(NEW.preferences, '$.cuisines') WHERE type <> 'text')
BEGIN
  SELECT RAISE(ABORT, 'preferences.cuisines must contain only strings');
END;

CREATE TRIGGER IF NOT EXISTS app_user_cuisines_are_text_on_update
BEFORE UPDATE OF preferences ON app_user
WHEN EXISTS (SELECT 1 FROM json_each(NEW.preferences, '$.cuisines') WHERE type <> 'text')
BEGIN
  SELECT RAISE(ABORT, 'preferences.cuisines must contain only strings');
END;

CREATE TABLE IF NOT EXISTS user_history (
  history_id INTEGER PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES app_user (user_id) ON DELETE CASCADE,
  timestamp  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  liked      INTEGER NOT NULL CHECK (liked IN (0, 1)),  -- UserHistory.like: 1 liked, 0 not
  recipe_id  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS user_history_by_user
  ON user_history (user_id, timestamp DESC);
"""

# A users database from before likes had a 0-5 rating per row instead: it becomes the like the model's training would
# label it with, 3 to 5 liked and 0 to 2 or none not.
RATINGS_TO_LIKES = """
BEGIN;
ALTER TABLE user_history ADD COLUMN liked INTEGER NOT NULL DEFAULT 0 CHECK (liked IN (0, 1));
UPDATE user_history SET liked = coalesce(rating >= 3, 0);
ALTER TABLE user_history DROP COLUMN rating;
COMMIT;
"""

DEMO_USERNAME = "demo"

# The demo user and its likes, injected with the schema when SEED_DEMO_USER is set. Its recipes are real ones of the
# 13k recipes CSV the recipe database is built from: of each kind, the one whose ingredient lines match the priced
# catalog best (lines matched / lines in the comments). Once the demo user has a history nothing is written, so a
# restart keeps it as it is.
DEMO_SEED = """
BEGIN;

INSERT INTO app_user (username, preferences)
VALUES ('demo', jsonb('{"special_diet": null, "cuisines": ["Italian", "Mexican"], "preferred_nutrient": "protein"}'))
ON CONFLICT (username) DO NOTHING;

WITH demo_history (recipe_id, liked) AS (VALUES
  ('openrecipes:5210', 1),  -- Roast Chicken with Carrots: 5 / 9
  ('openrecipes:8236', 1),  -- Baked Eggs with Tomato: 5 / 11
  ('openrecipes:9906', 1),  -- Avocado and Watercress Salad: 3 / 8
  ('openrecipes:1052', 1),  -- Meatballs and Noodle Soup (Almondigas): 4 / 11
  ('openrecipes:3911', 0)   -- Citrus Cookies: 4 / 11
)
INSERT INTO user_history (user_id, recipe_id, liked)
SELECT app_user.user_id, demo_history.recipe_id, demo_history.liked
FROM app_user, demo_history
WHERE app_user.username = 'demo'
  AND NOT EXISTS (SELECT 1 FROM user_history WHERE user_history.user_id = app_user.user_id);

COMMIT;
"""


def demo_enabled() -> bool:
    return os.getenv("SEED_DEMO_USER", "false").strip().lower() in {"1", "true", "yes"}


def open_user_database() -> sqlite3.Connection:
    """A plain sqlite3 connection to the users database, for the SQL scripts above."""
    connection = sqlite3.connect(user_database_path())
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_user_database() -> None:
    user_database_path().parent.mkdir(parents=True, exist_ok=True)
    connection = open_user_database()
    try:
        connection.executescript(SCHEMA)
        if "rating" in {row["name"] for row in connection.execute("PRAGMA table_info(user_history)")}:
            connection.executescript(RATINGS_TO_LIKES)
            logger.info("Turned the 0-5 ratings in %s into likes: 3 to 5 liked, the rest not.", user_database_path())
        if demo_enabled():
            changes = connection.total_changes
            connection.executescript(DEMO_SEED)
            if connection.total_changes > changes:
                logger.info("Injected the demo user %r and its liked and disliked recipes.", DEMO_USERNAME)
    finally:
        connection.close()
    create_tables(users_engine())
    logger.info("Users database ready at %s.", user_database_path())



def item_titles(item_ids: Iterable[str], catalog: RecipeRepository | None) -> dict[str, str]:
    """The title of each recipe among item_ids, from the recipe database, and of each catalog food, its description.
    An id neither holds, or whose database is unavailable, is left out."""
    ids = set(item_ids)
    titles: dict[str, str] = {}
    food_ids = [int(item_id) for item_id in ids if is_food_id(item_id)]
    if food_ids and catalog is not None:
        titles.update({str(food.fdc_id): food.description for food in catalog.foods_by_ids(food_ids)})
    recipe_ids = [item_id for item_id in ids if not is_food_id(item_id)]
    if recipe_ids:
        try:
            recipes = open_database()
        except HTTPException:
            return titles
        try:
            rows = recipes.execute(
                f"SELECT recipe_id, title FROM recipe WHERE recipe_id IN ({', '.join('?' * len(recipe_ids))})",
                recipe_ids,
            ).fetchall()
        finally:
            close_database(recipes)
        titles.update({row["recipe_id"]: row["title"] for row in rows if row["title"]})
    return titles


def known_item(recipe_id: str, catalog: RecipeRepository | None) -> bool:
    """Whether recipe_id is a recipe of the recipe database or, by its fdc_id, a food of the catalog (none without
    one)."""
    if is_food_id(recipe_id):
        return catalog is not None and catalog.food(int(recipe_id)) is not None
    recipes = open_database()
    try:
        return recipes.execute("SELECT 1 FROM recipe WHERE recipe_id = ?", (recipe_id,)).fetchone() is not None
    finally:
        close_database(recipes)


def rate_item(
    users: UserRepository,
    catalog: RecipeRepository | None,
    user_id: int,
    recipe_id: str,
    like: bool,
) -> UserHistory:
    """Record whether the user liked a recipe, or a catalog food by its fdc_id, in their history (UserRepository.
    add_history). LookupError for an id that is neither."""
    if not known_item(recipe_id, catalog):
        raise LookupError(f"Unknown recipe: {recipe_id}")
    return users.add_history(user_id, recipe_id, like)
