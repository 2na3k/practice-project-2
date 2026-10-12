from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from redis import Redis

from .cache import read_user_id, write_user_id
from .db import close_database, open_database
from .repository.recipe_repository import is_food_id, recipe_repository
from .schemas import Preferences, User, UserHistory

logger = logging.getLogger(__name__)

DEFAULT_USER_DATABASE_PATH = "/state/remymy-users.db"

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


def user_database_path() -> Path:
    return Path(os.getenv("USER_DATABASE_PATH", DEFAULT_USER_DATABASE_PATH))


def open_user_database() -> sqlite3.Connection:
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
    logger.info("Users database ready at %s.", user_database_path())


def find_user_id(connection: sqlite3.Connection, cache: Redis, username: str) -> int | None:
    user_id = read_user_id(cache, username)
    if user_id is not None:
        return user_id
    row = connection.execute(
        "SELECT user_id FROM app_user WHERE username = ?", (username,)
    ).fetchone()
    if row is None:
        return None
    write_user_id(cache, username, row["user_id"])
    return row["user_id"]


def username_exists(connection: sqlite3.Connection, cache: Redis, username: str) -> bool:
    return find_user_id(connection, cache, username) is not None


def fetch_user_history(
    connection: sqlite3.Connection,
    cache: Redis,
    username: str,
    limit: int = 50,
) -> list[UserHistory]:
    user_id = find_user_id(connection, cache, username)
    if user_id is None:
        return []
    rows = connection.execute(
        """
        SELECT user_id, timestamp, liked, recipe_id
        FROM user_history
        WHERE user_id = ?
        ORDER BY timestamp DESC, history_id DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return [
        UserHistory(user_id=row["user_id"], timestamp=row["timestamp"], like=bool(row["liked"]),
                    recipe_id=row["recipe_id"])
        for row in rows
    ]


def create_user(
    connection: sqlite3.Connection,
    cache: Redis,
    username: str,
    preferences: Preferences | None = None,
) -> User:
    preferences = preferences or Preferences()
    with connection:
        (row,) = connection.execute(
            "INSERT INTO app_user (username, preferences) VALUES (?, jsonb(?)) "
            "RETURNING user_id",
            (username, preferences.model_dump_json()),
        ).fetchall()
    write_user_id(cache, username, row["user_id"])
    logger.info("Created user %r with id %d.", username, row["user_id"])
    return User(id=row["user_id"], username=username, preferences=preferences)


def known_item(recipe_id: str) -> bool:
    """Whether recipe_id is a recipe of the recipe database or, by its fdc_id, a food of the catalog."""
    if is_food_id(recipe_id):
        return recipe_repository is not None and recipe_repository.food(int(recipe_id)) is not None
    recipes = open_database()
    try:
        return recipes.execute("SELECT 1 FROM recipe WHERE recipe_id = ?", (recipe_id,)).fetchone() is not None
    finally:
        close_database(recipes)


def add_history(
    connection: sqlite3.Connection,
    user_id: int,
    recipe_id: str,
    like: bool,
) -> UserHistory:
    """Record whether the user liked a recipe, or a catalog food by its fdc_id. One they rated before has its like
    changed and dated now; anything else gets a new row."""
    if not known_item(recipe_id):
        raise LookupError(f"Unknown recipe: {recipe_id}")

    entry = _modify_history(connection, user_id, recipe_id, like)
    if entry is None:
        with connection:
            (row,) = connection.execute(
                "INSERT INTO user_history (user_id, liked, recipe_id) VALUES (?, ?, ?) "
                "RETURNING timestamp",
                (user_id, int(like), recipe_id),
            ).fetchall()
        entry = UserHistory(user_id=user_id, timestamp=row["timestamp"], like=like, recipe_id=recipe_id)
    logger.debug("User %d %s recipe %s.", user_id, "liked" if like else "did not like", recipe_id)
    return entry


def _modify_history(
    connection: sqlite3.Connection,
    user_id: int,
    recipe_id: str,
    like: bool,
) -> UserHistory | None:
    """Change the like of the user's rows for the recipe and date them now; None when they have none."""
    with connection:
        rows = connection.execute(
            "UPDATE user_history SET liked = ?, timestamp = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE user_id = ? AND recipe_id = ? RETURNING timestamp",
            (int(like), user_id, recipe_id),
        ).fetchall()
    if not rows:
        return None
    return UserHistory(user_id=user_id, timestamp=rows[0]["timestamp"], like=like, recipe_id=recipe_id)
