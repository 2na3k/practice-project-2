"""The schema of remymy.db, the API's one database, and bringing it up to date at startup (init_database).

remymy.db holds everything: the Food.com recipes and FoodData Central tables the migration job loads (FOOD_SCHEMA, put
in by merge_food_data), the users with their history and saved recipes (users.SCHEMA), and the tables only the ORM
defines (models.create_tables): the priced catalog and the users' own recipes. Every recipe the app knows, whatever its
kind, has a row in recipe (RECIPE_SYNC keeps the catalog's and the users' there), so saved_recipe can refer to any.

The migration job imports FOOD_SCHEMA, FOOD_TABLES and merge_food_data from here too, so the schema has one owner."""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

# The food tables, created where missing. The migration job loads them; the recipe table also holds a row for each
# catalog food and user recipe.
FOOD_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_snapshot (
  snapshot_id TEXT PRIMARY KEY,
  source_name TEXT NOT NULL,
  source_version TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  license_text TEXT,
  retrieved_at TEXT NOT NULL,
  row_count INTEGER NOT NULL DEFAULT 0,
  schema_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_record (
  source_record_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL REFERENCES source_snapshot(snapshot_id),
  source_native_id TEXT NOT NULL,
  record_type TEXT NOT NULL,
  raw_payload TEXT NOT NULL,
  raw_hash TEXT NOT NULL,
  UNIQUE (snapshot_id, source_native_id, record_type)
);

CREATE TABLE IF NOT EXISTS quarantine_record (
  quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id TEXT NOT NULL REFERENCES source_snapshot(snapshot_id),
  source_native_id TEXT NOT NULL,
  record_type TEXT NOT NULL,
  reason TEXT NOT NULL,
  raw_payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recipe (
  recipe_id TEXT PRIMARY KEY,
  source_snapshot_id TEXT REFERENCES source_snapshot(snapshot_id),
  source_record_id TEXT UNIQUE REFERENCES source_record(source_record_id),
  title TEXT,
  instructions_raw TEXT,
  description TEXT,
  cuisine TEXT,
  prep_minutes INTEGER,
  cook_minutes INTEGER,
  total_minutes INTEGER,
  yield_text TEXT,
  servings REAL,
  quality_status TEXT NOT NULL,
  raw_hash TEXT,
  normalization_version TEXT,
  -- openrecipes: a Food.com recipe the ingest loaded, with its source record; catalog: a priced catalog food, by its
  -- fdc_id; user: a recipe a user wrote ("user-recipe:<n>"). Every recipe the app knows has a row, so saved_recipe can
  -- refer to any of them.
  kind TEXT NOT NULL DEFAULT 'openrecipes' CHECK (kind IN ('openrecipes', 'catalog', 'user')),
  CHECK (kind <> 'openrecipes' OR source_record_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS recipe_ingredient (
  ingredient_id TEXT PRIMARY KEY,
  recipe_id TEXT NOT NULL REFERENCES recipe(recipe_id),
  position INTEGER NOT NULL,
  raw_text TEXT NOT NULL,
  quantity_value REAL,
  quantity_min REAL,
  quantity_max REAL,
  unit_raw TEXT,
  unit_normalized TEXT,
  ingredient_text TEXT,
  preparation TEXT,
  optional INTEGER,
  parse_status TEXT NOT NULL,
  parse_confidence REAL
);

CREATE TABLE IF NOT EXISTS recipe_instruction (
  recipe_id TEXT NOT NULL REFERENCES recipe(recipe_id),
  position INTEGER NOT NULL,
  text TEXT NOT NULL,
  PRIMARY KEY (recipe_id, position)
);

CREATE TABLE IF NOT EXISTS food_category (
  id TEXT PRIMARY KEY,
  code TEXT,
  description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS measure_unit (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nutrient (
  nutrient_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  unit_name TEXT NOT NULL,
  nutrient_number TEXT,
  rank INTEGER
);

CREATE TABLE IF NOT EXISTS food (
  fdc_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL REFERENCES source_snapshot(snapshot_id),
  data_type TEXT NOT NULL,
  description TEXT,
  food_category_id TEXT,
  publication_date TEXT,
  foundation_flag INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS foundation_food (
  fdc_id TEXT PRIMARY KEY REFERENCES food(fdc_id),
  ndb_number TEXT,
  footnote TEXT
);

CREATE TABLE IF NOT EXISTS food_nutrient (
  observation_id TEXT PRIMARY KEY,
  fdc_id TEXT NOT NULL REFERENCES food(fdc_id),
  nutrient_id TEXT NOT NULL REFERENCES nutrient(nutrient_id),
  snapshot_id TEXT NOT NULL REFERENCES source_snapshot(snapshot_id),
  amount REAL,
  data_points INTEGER,
  derivation_id TEXT,
  min_amount REAL,
  max_amount REAL,
  median_amount REAL,
  footnote TEXT,
  min_year_acquired INTEGER
);

CREATE TABLE IF NOT EXISTS food_portion (
  portion_id TEXT PRIMARY KEY,
  fdc_id TEXT REFERENCES food(fdc_id),
  seq_num INTEGER,
  amount REAL,
  measure_unit_id TEXT REFERENCES measure_unit(id),
  portion_description TEXT,
  modifier TEXT,
  gram_weight REAL,
  data_points INTEGER,
  footnote TEXT,
  min_year_acquired INTEGER
);

CREATE TABLE IF NOT EXISTS food_lineage (
  lineage_id TEXT PRIMARY KEY,
  fdc_id TEXT REFERENCES food(fdc_id),
  fdc_of_input_food TEXT,
  ingredient_code TEXT,
  ingredient_description TEXT,
  unit TEXT,
  portion_code TEXT,
  portion_description TEXT,
  gram_weight REAL,
  retention_code TEXT
);

CREATE TABLE IF NOT EXISTS ingredient_food_match (
  ingredient_id TEXT NOT NULL REFERENCES recipe_ingredient(ingredient_id),
  fdc_id TEXT NOT NULL REFERENCES food(fdc_id),
  candidate_rank INTEGER NOT NULL,
  match_method TEXT NOT NULL,
  match_score REAL NOT NULL,
  accepted INTEGER NOT NULL DEFAULT 0,
  review_status TEXT NOT NULL DEFAULT 'unreviewed',
  mapping_version TEXT NOT NULL,
  PRIMARY KEY (ingredient_id, fdc_id, mapping_version)
);

CREATE TABLE IF NOT EXISTS recipe_nutrition (
  recipe_id TEXT NOT NULL REFERENCES recipe(recipe_id),
  nutrient_id TEXT NOT NULL REFERENCES nutrient(nutrient_id),
  amount REAL,
  unit TEXT,
  basis TEXT NOT NULL,
  estimated INTEGER NOT NULL DEFAULT 1,
  ingredient_coverage_pct REAL,
  mass_coverage_pct REAL,
  serving_count REAL,
  confidence REAL,
  calculation_version TEXT,
  calculated_at TEXT,
  PRIMARY KEY (recipe_id, nutrient_id, basis, calculation_version)
);

CREATE INDEX IF NOT EXISTS recipe_title_idx ON recipe(title);
CREATE INDEX IF NOT EXISTS recipe_quality_idx ON recipe(quality_status);
CREATE INDEX IF NOT EXISTS recipe_kind_idx ON recipe(kind);
CREATE INDEX IF NOT EXISTS recipe_ingredient_recipe_idx ON recipe_ingredient(recipe_id, position);
CREATE INDEX IF NOT EXISTS recipe_ingredient_text_idx ON recipe_ingredient(ingredient_text);
CREATE INDEX IF NOT EXISTS food_description_idx ON food(description);
CREATE INDEX IF NOT EXISTS food_foundation_idx ON food(foundation_flag, data_type);
CREATE INDEX IF NOT EXISTS food_nutrient_food_idx ON food_nutrient(fdc_id, nutrient_id);
CREATE INDEX IF NOT EXISTS food_portion_food_idx ON food_portion(fdc_id);
"""

# The food tables in an order that puts every table after the ones it refers to, for merge_food_data.
FOOD_TABLES = (
    "source_snapshot", "source_record", "quarantine_record", "recipe", "recipe_ingredient", "recipe_instruction",
    "food_category", "measure_unit", "nutrient", "food", "foundation_food", "food_nutrient", "food_portion",
    "food_lineage", "ingredient_food_match", "recipe_nutrition",
)

# What init_database has done once and must not do again, such as importing the old users database.
META_SCHEMA = """
CREATE TABLE IF NOT EXISTS remymy_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# A catalog food or a user's recipe gets its row in recipe when it is added, and a user's recipe loses it when deleted
# (and with it the saves of it, by saved_recipe's ON DELETE CASCADE). Run after models.create_tables, which creates
# the tables they watch. A catalog food is never deleted: the catalog only grows.
RECIPE_SYNC = """
CREATE TRIGGER IF NOT EXISTS catalog_food_is_a_recipe
AFTER INSERT ON price_mapped_nutrients
BEGIN
  INSERT INTO recipe (recipe_id, title, quality_status, kind)
  VALUES (CAST(NEW.fdc_id AS TEXT), NEW.description, 'accepted', 'catalog')
  ON CONFLICT (recipe_id) DO UPDATE SET title = excluded.title WHERE recipe.kind = 'catalog';
END;

CREATE TRIGGER IF NOT EXISTS user_recipe_is_a_recipe
AFTER INSERT ON user_recipe
BEGIN
  INSERT INTO recipe (recipe_id, title, quality_status, kind)
  VALUES ('user-recipe:' || NEW.user_recipe_id, NEW.title, 'accepted', 'user')
  ON CONFLICT (recipe_id) DO UPDATE SET title = excluded.title WHERE recipe.kind = 'user';
END;

CREATE TRIGGER IF NOT EXISTS user_recipe_title_is_the_recipes
AFTER UPDATE OF title ON user_recipe
BEGIN
  UPDATE recipe SET title = NEW.title WHERE recipe_id = 'user-recipe:' || NEW.user_recipe_id;
END;

CREATE TRIGGER IF NOT EXISTS user_recipe_deleted_leaves_the_recipes
AFTER DELETE ON user_recipe
BEGIN
  DELETE FROM recipe WHERE recipe_id = 'user-recipe:' || OLD.user_recipe_id;
END;
"""

# The rows RECIPE_SYNC would have added for the catalog foods and user recipes stored before it existed.
RECIPE_BACKFILL = """
INSERT INTO recipe (recipe_id, title, quality_status, kind)
SELECT CAST(fdc_id AS TEXT), description, 'accepted', 'catalog' FROM price_mapped_nutrients WHERE true
ON CONFLICT (recipe_id) DO NOTHING;

INSERT INTO recipe (recipe_id, title, quality_status, kind)
SELECT 'user-recipe:' || user_recipe_id, title, 'accepted', 'user' FROM user_recipe WHERE true
ON CONFLICT (recipe_id) DO NOTHING;
"""

DEFAULT_LEGACY_FOOD_DATABASE_PATH = "/data/remymy-food.db"
DEFAULT_LEGACY_USER_DATABASE_PATH = "/state/remymy-users.db"
LEGACY_USERS_IMPORTED = "legacy_users_imported"


def legacy_food_database_path() -> Path:
    """The recipe database of before remymy.db, which the migration job built on its own; read once if remymy.db has
    no recipes yet."""
    return Path(os.getenv("LEGACY_FOOD_DATABASE_PATH", DEFAULT_LEGACY_FOOD_DATABASE_PATH))


def legacy_user_database_path() -> Path:
    """The users database of before remymy.db; its users, history and recipes are imported once."""
    return Path(os.getenv("LEGACY_USER_DATABASE_PATH", DEFAULT_LEGACY_USER_DATABASE_PATH))


def read_only(path: Path) -> str:
    """The URI that opens the database at path read-only and never writes beside it, as the old databases need: they
    sit on read-only mounts, and one in WAL mode cannot otherwise be read there (SQLite would make its -shm file)."""
    return f"file:{quote(str(Path(path).resolve()), safe='/')}?mode=ro&immutable=1"


def connect(path: Path) -> sqlite3.Connection:
    """A plain sqlite3 connection to the database at path, for the SQL scripts here, with foreign keys enforced. URIs
    are on, so ATTACH can take read_only's."""
    connection = sqlite3.connect(f"file:{quote(str(path), safe='/')}", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _columns(connection: sqlite3.Connection, schema: str, table: str) -> list[sqlite3.Row]:
    return connection.execute(f'PRAGMA "{schema}".table_info("{table}")').fetchall()


def _copy_rows(connection: sqlite3.Connection, source: str, table: str, conflict: str, where: str = "") -> int:
    """Insert source.table's rows into main.table, by the columns both have; conflict says what a row whose key main
    holds already does: "update" takes the source's values (an upsert: the row is changed, never deleted, so nothing
    that refers to it cascades away) and "ignore" keeps main's."""
    target = _columns(connection, "main", table)
    source_names = {row["name"] for row in _columns(connection, source, table)}
    if not source_names:
        return 0
    names = [row["name"] for row in target if row["name"] in source_names]
    keys = [row["name"] for row in sorted(target, key=lambda row: row["pk"]) if row["pk"]]
    listed = ", ".join(f'"{name}"' for name in names)
    updates = [name for name in names if name not in keys]
    if conflict == "update" and updates:
        action = "DO UPDATE SET " + ", ".join(f'"{name}" = excluded."{name}"' for name in updates)
    else:
        action = "DO NOTHING"
    # WHERE true lets SQLite tell the ON CONFLICT of the upsert from a join's ON
    cursor = connection.execute(
        f'INSERT INTO main."{table}" ({listed}) SELECT {listed} FROM "{source}"."{table}" WHERE {where or "true"} '
        f'ON CONFLICT ({", ".join(keys)}) {action}'
    )
    return cursor.rowcount


def has_food_data(connection: sqlite3.Connection) -> bool:
    """Whether the database holds the Food.com recipes the migration job loads."""
    return connection.execute("SELECT 1 FROM recipe WHERE kind = 'openrecipes' LIMIT 1").fetchone() is not None


def rebuild_recipe_fts(connection: sqlite3.Connection) -> bool:
    """Index the Food.com recipes for full-text search again; False when this SQLite has no FTS5."""
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS recipe_fts USING fts5(recipe_id UNINDEXED, title, ingredients, instructions)"
        )
    except sqlite3.OperationalError:
        return False
    with connection:
        connection.execute("DELETE FROM recipe_fts")
        connection.execute(
            "INSERT INTO recipe_fts(recipe_id, title, ingredients, instructions) "
            "SELECT r.recipe_id, coalesce(r.title, ''), "
            "coalesce((SELECT group_concat(raw_text, ' ') FROM recipe_ingredient i WHERE i.recipe_id = r.recipe_id), ''), "
            "coalesce(r.instructions_raw, '') FROM recipe r WHERE r.kind = 'openrecipes'"
        )
    return True


def merge_food_data(connection: sqlite3.Connection, source: Path) -> dict[str, int]:
    """Put the food tables of the database at source (an ingest's output, or the old recipe database) into the one
    connection is on, in one transaction. A row whose key is there already is updated, never replaced or deleted, so
    the saves and history that refer to a recipe stay; rows the source does not have stay too. Returns the rows each
    table took."""
    connection.execute("ATTACH DATABASE ? AS source", (read_only(source),))
    try:
        with connection:
            counts = {table: _copy_rows(connection, "source", table, "update") for table in FOOD_TABLES}
    finally:
        connection.execute("DETACH DATABASE source")
    rebuild_recipe_fts(connection)
    logger.info("Merged the food data of %s: %s.", source,
                ", ".join(f"{table} {count}" for table, count in counts.items() if count))
    return counts


def import_legacy_users(connection: sqlite3.Connection, source: Path) -> dict[str, int]:
    """Copy the users, their history and their own recipes from the users database of before remymy.db, ids and all,
    into a database that has no users yet. A history from before likes has its 0-5 ratings turned into likes, 3 to 5
    liked; a budget stored as a whole number is stored as the float the schema takes. A row the schema rejects is left
    out (and so is what refers to it), and counted in the log."""
    connection.execute("ATTACH DATABASE ? AS legacy", (read_only(source),))
    counts: dict[str, int] = {}
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM legacy.sqlite_master WHERE type = 'table'")}
        with connection:
            if "app_user" in tables:
                counts["app_user"] = connection.execute(
                    "INSERT OR IGNORE INTO main.app_user (user_id, username, preferences, created_at) "
                    "SELECT user_id, username, CASE WHEN json_type(preferences, '$.budget') = 'integer' "
                    "THEN jsonb_set(preferences, '$.budget', json_extract(preferences, '$.budget') * 1.0) "
                    "ELSE jsonb(preferences) END, created_at FROM legacy.app_user"
                ).rowcount
            if "user_history" in tables:
                history = {row["name"] for row in _columns(connection, "legacy", "user_history")}
                liked = "liked" if "liked" in history else "coalesce(rating >= 3, 0)"
                counts["user_history"] = connection.execute(
                    "INSERT OR IGNORE INTO main.user_history (history_id, user_id, timestamp, liked, recipe_id) "
                    f"SELECT history_id, user_id, timestamp, {liked}, recipe_id FROM legacy.user_history "
                    "WHERE user_id IN (SELECT user_id FROM main.app_user)"
                ).rowcount
            if "user_recipe" in tables:
                counts["user_recipe"] = _copy_rows(connection, "legacy", "user_recipe", "ignore",
                                                   "user_id IN (SELECT user_id FROM main.app_user)")
            if "user_recipe_tag" in tables:
                counts["user_recipe_tag"] = _copy_rows(connection, "legacy", "user_recipe_tag", "ignore",
                                                       "user_recipe_id IN (SELECT user_recipe_id FROM main.user_recipe)")
        if "app_user" in tables:
            stored = connection.execute("SELECT count(*) FROM legacy.app_user").fetchone()[0]
            if stored > counts["app_user"]:
                logger.warning("%d of the %d users in %s did not fit the schema and were left out.",
                               stored - counts["app_user"], stored, source)
    finally:
        connection.execute("DETACH DATABASE legacy")
    logger.info("Imported the users database %s: %s.", source,
                ", ".join(f"{table} {count}" for table, count in counts.items()) or "nothing")
    return counts


def _done(connection: sqlite3.Connection, key: str) -> bool:
    return connection.execute("SELECT 1 FROM remymy_meta WHERE key = ?", (key,)).fetchone() is not None


def _mark_done(connection: sqlite3.Connection, key: str, value: str) -> None:
    with connection:
        connection.execute("INSERT INTO remymy_meta (key, value) VALUES (?, ?) "
                           "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))


def init_database() -> None:
    """Bring remymy.db up to date, as the API does at startup: its tables are created where missing; the first time, the
    old recipe and users databases are imported when they are there; then the users' data is migrated (and the demo
    user seeded, with SEED_DEMO_USER), the catalog takes the catalog CSV's new foods, and every catalog food and user
    recipe has its row in recipe."""
    # imported here: users and the repositories import db, which imports nothing of this module's
    from .db import database_engine, database_path
    from .models import create_tables
    from .repository.recipe_repository import init_catalog_database
    from .users import SCHEMA as USER_SCHEMA, migrate_user_data

    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(path)
    try:
        # readers (the recipe routes' plain connections) and the ORM's writers do not block each other
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(FOOD_SCHEMA + META_SCHEMA + USER_SCHEMA)
        create_tables(database_engine())
        connection.executescript(RECIPE_SYNC)
        if not has_food_data(connection) and legacy_food_database_path().is_file():
            merge_food_data(connection, legacy_food_database_path())
        if not _done(connection, LEGACY_USERS_IMPORTED):
            legacy = legacy_user_database_path()
            has_users = connection.execute("SELECT 1 FROM app_user LIMIT 1").fetchone() is not None
            if legacy.is_file() and not has_users:
                import_legacy_users(connection, legacy)
            # done for good either way: a users database that turns up later would clash with the users made since
            _mark_done(connection, LEGACY_USERS_IMPORTED, str(legacy) if legacy.is_file() else "")
        migrate_user_data(connection)
    finally:
        connection.close()
    init_catalog_database()
    connection = connect(path)
    try:
        with connection:
            connection.executescript(RECIPE_BACKFILL)
    finally:
        connection.close()
    logger.info("Database ready at %s.", path)
