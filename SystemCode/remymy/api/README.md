# Remymy Recipe API

This is the narrow, read-only bridge between the normalized SQLite food
database and the Remymy frontend. It uses FastAPI, Pydantic response models,
and parameterized SQLite queries.

## Project layout

- `src/remymy_api/` contains the FastAPI app, routes, database access, and
  response schemas. Request and response models (DTOs, Pydantic) are in
  `schemas.py`; SQLAlchemy ORM models are in `models.py`, with their `Base`,
  engine and `get_db` session dependency in `db.py`.
- `src/remymy_api/repository/` holds the repositories, each on a SQLAlchemy
  session: `RecipeRepository` for the priced food catalog (see
  [Catalog database](#catalog-database)) and `UserRepository` for users and
  their history (see [Users database](#users-database)).
  `src/remymy_api/dependencies.py` gives a route its repositories for the
  request; tests swap them with `app.dependency_overrides`.
- `src/remymy_api/inference/` runs the recommendation model in the API process:
  `service.py` is what the recommend route calls, over the catalog the
  repository serves; `model_inference_score_recommend.py` is the HNSW retrieval
  and the window Transformer + cross-attention reranker of
  `ml_models/training/model_training_reranker_avgemb.ipynb`.
  `src/remymy_api/inference/weights/window_transformer_reranker/` holds that trained model; the older
  windowed GRU in `src/remymy_api/inference/weights/gru_xattn_reranker/` still loads with
  `ARTIFACT_DIR=src/remymy_api/inference/weights/gru_xattn_reranker`.
- `migrations/ingest_food_data.py` ingests Open Recipes 13k and FoodData
  Central Foundation CSVs without doing ingredient matching or nutrition
  inference.
- `migrations/bootstrap.py` validates an existing database or builds a
  missing database from explicitly supplied source paths.
- `tests/` contains the focused API tests.

## Public contract

- `GET /health` returns `{ "status": "ok" }` while the API process is running.
- `GET /ready` returns `{ "status": "ok" }` only when the normalized recipe
  database exists and has the required recipe tables.
- `GET /api/recipes?q=&limit=&offset=` returns `{ items, total, limit, offset }`.
  Each item contains only `id`, `title`, `ingredient_count`,
  `ingredient_preview`, and `quality_status`.
- `GET /api/recipes/{id}` returns only `id`, `title`, `ingredients`,
  `instructions`, and `quality_status`.

`limit` is between 1 and 100, and `offset` must be non-negative. Search uses
case-insensitive matching against recipe titles and raw ingredient text.
Recipe summaries exclude blank-title records and include at most three preview
ingredients; detail responses preserve all stored ingredient and instruction
strings in position order.

The API does not expose FoodData Central rows, raw source payloads, source
records, quarantine rows, nutrition, or arbitrary SQL.

### Recipe classification

- `POST /api/classify` with `{ "query": "Grilled chicken breast, skinless, 165 kcal, 31g protein" }`
  tags a food with recipe classes (`easy`, `breakfast`, `protein_centerpiece`, …; `generic` is
  exclusive). Nutrients are per 100 g and may be written into the query or sent as
  `nutrients: { "energy_kcal": 165 }`, which overrides the query; any left out take the training
  medians. `product` / `adj` / `verb` lists skip term extraction for that field. A query that is
  only an fdc_id (`"fdc:167516"`) looks up a training food. The response holds `labels`, `scores`
  for every label, and the terms and nutrients the model used.
- `GET /api/classify/{fdc_id}` classifies a training food from the fdc_id lookup and also returns
  its `training_labels`.

A query without a food description is `422`, an fdc_id that is not a training food `404`. The
classifier loads at startup from `CLASSIFIER_ARTIFACT_DIR` (default `src/remymy_api/inference/weights/recipe_classifier`)
with the lookup at `FDC_LOOKUP_PATH` (default `src/remymy_api/inference/weights/fdc_lookup.json`, written by
`src/remymy_api/inference/build_fdc_lookup.py`). Its packages are in `requirements-classifier.txt`,
not in the image: without them or the weights, both routes answer `503`. The first free-text
query loads GLiNER and stanza (about 20 s, and stanza downloads its English model once); later
queries take well under a second, one at a time.

## Database

Everything the API keeps is in one SQLite file, `remymy.db` at `DATABASE_PATH`
(default `/state/remymy.db`, on the `remymy-user-data` volume in compose): the
Food.com recipes and FoodData Central tables, the priced catalog, the users,
their history and their saved recipes. One file lets the tables refer to each
other with real foreign keys. `src/remymy_api/schema.py` owns the schema, which
needs SQLite 3.45 or newer for JSONB. On startup `init_database` creates the
tables that are missing, adds the catalog CSV's new foods, and migrates the
users' data. The first time, it also imports the old databases when they are
there: the recipes of `LEGACY_FOOD_DATABASE_PATH` (default
`/data/remymy-food.db`) and the users, history and recipes of
`LEGACY_USER_DATABASE_PATH` (default `/state/remymy-users.db`). After that
neither file is read.

Every recipe the app knows has a row in `recipe`, whatever its `kind`:
`openrecipes` for the Food.com recipes the migration job loads, `catalog` for
each catalog food (by its `fdc_id`), and `user` for a recipe a user wrote
(`user-recipe:<n>`). Triggers (`schema.RECIPE_SYNC`) add the catalog's and the
users' rows, and remove a user recipe's row when it is deleted. The recipe
routes list and show only the `openrecipes` ones.

The container listens on port `8002`. `/health` reports process liveness, while
`/ready` reports readiness only once the database holds the recipes. Recipe
routes return a generic `503 Recipe data is unavailable.` when the file is
missing or lacks the recipe tables. No local path or SQLite error is returned to
callers.

## Users and saved recipes

`UserRepository` (`src/remymy_api/repository/user_repository.py`) reads and
writes users, their history and their saved recipes through the ORM:
`models.AppUser` (its `preferences` written with `jsonb()` and read with
`json()`), `models.UserHistoryEntry` and `models.SavedRecipe`. It looks a
username up in Redis before the database, and each write commits its own
transaction. `app_user`, `user_history` and `saved_recipe` are still created by
the SQL in `users.py` (`SCHEMA`), for the JSON checks and triggers on
`preferences`. `users.py` also keeps the rating migration, the demo user, and
`rate_item`, which checks a rated id is a recipe or a catalog food before
`UserRepository.add_history` records it.

`saved_recipe` (`user_id` → `app_user`, `recipe_id` → `recipe`, both
`ON DELETE CASCADE`) holds the recipes a user saved for later, of any kind.
`GET /api/users/{username}/saved` lists them newest first. `PUT` and `DELETE`
on `/api/users/{username}/saved/{recipe_id}` save one or take it out; saving
one twice keeps it as it was. Deleting a user or a recipe deletes its saves.

The recipes users write themselves are ORM-only tables (`models.py`):
`user_recipe` (ingredient and step lines as JSON arrays, a column per nutrient
per 100 g) and `user_recipe_tag` (each tag and whether the classifier suggested
it or the user added it). `create_tables` creates them on startup after the SQL
schema. `POST /api/recipes` (body `UserRecipeCreate`, response
`UserRecipeResponse`) is declared and answers `501` until it is implemented.

## Catalog

`RecipeRepository` (`src/remymy_api/repository/recipe_repository.py`)
reads the priced food catalog, the `price_mapped_nutrients` table of
`remymy.db`, through the ORM (`models.RecipeRecord`). On startup
`init_catalog_database` brings it up to date with the catalog CSV at
`RECIPE_PATH` (default `/data/price_mapped_nutrients.csv`):
`RecipeRepository.from_csv_to_db` creates the table if it is missing and
inserts only the CSV rows whose `fdc_id` it does not hold; stored rows are not
updated. Lists and dicts keep the CSV's Python-literal text. Without the CSV the
stored catalog is used as it is. It exposes `fdc_ids`, `all_foods`,
`foods_by_ids`, `food` and `search` (case-insensitive on the description), and
`digest`, which turns a recipe's ingredient lines into a `DigestedRecipe`
(`src/remymy_api/recipe.py`): the priced foods the lines match, each line
normalized by `name_key` and looked up in the ingredient index, and the mean
of their nutrients. The index is built once per catalog database and shared
by every request's repository: the API builds it at startup with
`load_ingredient_index()`, and inserting new foods drops it to be rebuilt. The
recommend route puts the user's likes in their `History`
(`src/remymy_api/history.py`) and digests each of its recipes once for the
inference service.

A route gets a repository on its request's session from
`dependencies.get_recipe_repository`, or `None` when there is no catalog
database (a missing file is not opened, so SQLite does not create it empty).
On startup the API opens one for the inference service, which loads the
model from `ARTIFACT_DIR` (default
`src/remymy_api/inference/weights/window_transformer_reranker`) and builds the HNSW index over `all_foods()`. The
index is saved under `INDEX_DIR` and reused while the model files and the
catalog's foods are unchanged. With neither the CSV nor the database the API
still starts, and `GET /api/recommend/{username}` answers `503`.

## Try it in Swagger

Open http://localhost:8002/docs (or just http://localhost:8002/). With
`SEED_DEMO_USER=true`, which compose sets by default, the API injects a `demo`
user and its likes into the users database with its schema on startup
(`DEMO_SEED` in `src/remymy_api/users.py`), unless the demo user already has a
history. The likes are of five real recipes of the 13k recipes CSV, chosen
because their ingredient lines match the priced catalog best.
`GET /api/recommend/{username}` comes pre-filled with `demo`, so "Try it out",
then "Execute" returns its buying profile and recommendations.

## Run locally

From this directory:

```bash
python -m pip install -r requirements.txt
python3 serve.py
```

`serve.py` fills in local defaults for anything you have not set:
`DATABASE_PATH=./data/remymy.db`,
`LEGACY_FOOD_DATABASE_PATH=../data/remymy-food.db`,
`LEGACY_USER_DATABASE_PATH=./data/remymy-users.db`,
`RECIPE_PATH=../data/price_mapped_nutrients.csv`,
`REDIS_URL=redis://localhost:6379/1` and `SEED_DEMO_USER=true`. Redis only
caches user ids and recommendations; without it the API still answers, running
the model on every request.

The API logs to stderr, one line per record with time, level and module, next to
uvicorn's own lines. `LOG_LEVEL` (default `INFO`) sets the level: `INFO` covers
startup, the catalog and model loading, and each computed recommendation with
its timing; failures that answer a generic `503` are logged with their cause;
`DEBUG` adds cache hits, ingredient matching and every step of the pipeline.

## Bootstrap and ingestion

The bootstrap command (`api-migrate` in compose) makes sure `remymy.db` holds
the recipes. It is idempotent: a database that holds them passes SQLite
`quick_check` and the recipe-table checks, then the command exits 0. A database
without them gets the old recipe database's when there is one
(`--legacy-database`, default `LEGACY_FOOD_DATABASE_PATH`). Otherwise the
command ingests the sources, which must be supplied explicitly:

```bash
DATABASE_PATH=/state/remymy.db \
RECIPES_SOURCE=/data/13k-recipes.db \
FDC_SOURCE_DIR=/data/FoodData_Central_foundation_food_csv_2026-04-30 \
python migrations/bootstrap.py
```

The same values can be passed with `--database-path`, `--recipes`, and
`--fdc-dir`. The ingest is written to a temporary file next to `remymy.db` and
validated. `schema.merge_food_data` then merges it in, in one transaction, with
upserts: a row already there is updated, never replaced or deleted, so the
saves and history that refer to a recipe stay. The file is never swapped, as it
also holds the users. Use `--validate-only` to prohibit loading.

The ingestion CLI can also be run directly:

```bash
python migrations/ingest_food_data.py \
  --recipes /data/13k-recipes.db \
  --fdc-dir /data/FoodData_Central_foundation_food_csv_2026-04-30 \
  --fdc-release 2026-04 \
  --output /tmp/food-ingest.db
```
