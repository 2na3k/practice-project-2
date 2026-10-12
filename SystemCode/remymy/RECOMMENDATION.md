# Recommendations: model and API

Remymy recommends grocery foods to a user based on the recipes they have
cooked and rated. The foods come from a catalog of 10,658 USDA FoodData Central
(FDC) foods priced from FairPrice, in SGD. A trained reranker scores how likely
the user is to like each food, and the API also returns a *buying profile*: the
average price and nutrients of the foods the user's recipes call for.

The model runs inside the Data API. The recommend route is the controller, the
inference service ([`api/src/remymy_api/inference/service.py`](api/src/remymy_api/inference/service.py))
runs the model, and `RecipeRepository`
([`api/src/remymy_api/repository/recipe_repository.py`](api/src/remymy_api/repository/recipe_repository.py))
serves it the catalog through the one shared instance, `recipe_repository`.

| Component | Code | Port | Role |
| --- | --- | --- | --- |
| Data API | [`api/`](api/) | 8002 | Users, history, recipes, the catalog, the recommendation model, caching |
| Redis | `redis` in [`docker-compose.yml`](docker-compose.yml) | 6379 (internal) | Cache for user ids and recommendations |

```mermaid
sequenceDiagram
    participant C as Client
    participant A as Data API :8002
    participant R as Redis
    participant U as Users DB (SQLite)
    participant D as Recipe DB (SQLite)
    participant M as Inference service (in the API)

    C->>A: GET /api/recommend/{username}?top_k=10
    A->>R: user:{username}
    alt not in Redis
        A->>U: look up user_id
        A->>R: store user:{username} (1 day TTL)
    end
    A->>U: newest 50 history rows
    A->>R: recommendations:{user_id}
    alt cache miss
        A->>D: ingredient lines of each history recipe
        A->>A: the likes go into a History; recipe_repository digests each recipe into a Recipe
        A->>M: recommend(history, recipes, top_k=50)
        M-->>A: scored items
        A->>A: compute UserBuyingProfile from the History
        A->>R: store recommendations:{user_id} (1 day TTL)
    end
    A-->>C: profile + first top_k items, or 503 when no catalog is loaded
```

---

## The model

### What it predicts

The model is a **GRU + cross-attention reranker**. Given a user's dated, rated
history and a candidate item, it outputs **P(like)**: the probability that the
user would rate the candidate 3 to 5 stars. It only scores candidates. Choosing
them is the retrieval step described under
[How the API uses it](#how-the-api-uses-it).

The design spec is `recipe_reranker_spec.md` in the ML development folder. The
training notebook is
[`ml_models/training/model_training_reranker_avgemb.ipynb`](ml_models/training/model_training_reranker_avgemb.ipynb).

### Inputs

Every item, whether a history item or a candidate, is described by two things:

- **Text:** the ingredient phrases of the item, split by an NER step into
  `product`, `adj` and `verb` fields (for example "winter squash" is a product,
  "fresh" an adjective, "chopped" a verb). Words are lowercased, lemmatized with
  the field's part of speech, singularized and de-duplicated, up to 40 tokens.
  The vocabulary has 1,906 tokens.
- **Numbers:** `log1p` of `[price, calories, total fat, sugar, sodium, protein,
  saturated fat, carbohydrates]`, z-scored with training statistics.

The user is described by:

- **Profile:** the mean of those eight `log1p` numbers over the whole history,
  z-scored.
- **History windows:** the history in date order, cut into windows wherever two
  ratings are 183 days (about six months) or more apart, so each window is one
  period of the user's taste. The last 10 windows are kept, with the last 20
  items in each. Each item carries its rating, centered on the user's mean
  rating. A missing rating counts as the mean.

### Architecture

```
item tokens + [price, nutrients] ──► ItemEncoder ──► item_emb (64)

history item_emb + centered ratings, per window ──► GRU (restarted per window)
                                                     ──► one state per window

mean [price, nutrients] of the history ──► UserEncoder (MLP) ──► profile token

candidate item_emb ──► cross-attention over [profile token; window states]
                   ──► MLP(concat(attention output, candidate item_emb))
                   ──► logit ──► sigmoid ──► P(like)
```

- **ItemEncoder:** token and field embeddings, one self-attention layer
  (no positional encoding, since ingredient order carries no meaning), mean
  pooling, a linear projection of the eight numbers, then a linear fusion to 64
  dimensions.
- **UserEncoder:** Linear → ReLU → Linear from the 8 profile numbers to 64.
- **History encoder:** each window's step input is `item_emb +
  Linear(centered rating)`. The GRU starts from zero in every window, and each
  window's final hidden state is its state.
- **Reranker:** the candidate attends (4 heads) over the profile token and the
  window states. Learned embeddings mark which key is the profile and how recent
  each window is.

| Setting | Value |
| --- | --- |
| Embedding size `d` | 64 |
| Attention heads | 4 |
| Text self-attention layers | 1 |
| Max tokens per item | 40 |
| Windows × items per window | 10 × 20 |
| Window gap | 183 days |
| Dropout | 0.1 |

### Training

- **Data:** Food.com ratings joined to Food.com recipes, whose ingredients were
  priced from FairPrice by
  [`ml_models/preprocessing/map_recipe_nutrients.ipynb`](ml_models/preprocessing/map_recipe_nutrients.ipynb).
  Recipe totals range from 3.08 to 426 SGD.
- **Labels:** ratings 3 to 5 are positive, 0 to 2 negative. A Food.com rating
  of 0 means a review without stars and counts as negative.
- **Users:** only users with at least 6 ratings are kept: 10,450 users and
  356,422 ratings, 96.4% of them positive.
- **Negatives:** each positive is paired with one recipe the user never rated,
  drawn in proportion to popularity so that popularity alone cannot separate the
  classes.
- **Split:** per user, the last rating is test, the second-to-last validation,
  the rest training.
- **No leakage:** a target on day *t* only sees ratings from days strictly
  before *t*, for its history, its profile and its rating centering.
- **Optimization:** AdamW, learning rate 1e-3, weight decay 1e-5, batch size
  512, binary cross-entropy, early stopping on validation log loss. The best
  epoch was 5 of at most 10.

Artifacts in [`api/weights/gru_xattn_reranker/`](api/weights/gru_xattn_reranker/), where the API loads them from:

| File | Contents |
| --- | --- |
| `model.pt` | Trained weights |
| `config.json` | Hyperparameters, vocabulary, normalization statistics, test metrics |
| `item_cache.pt` | Embeddings of the 99,763 training recipes (not used when serving) |
| `training_history.csv` | Loss and AUC per epoch |

### Test results

Classification, over all test rows:

| Metric | Value |
| --- | --- |
| ROC-AUC | 0.625 |
| AUC, positives vs sampled negatives | 0.634 |
| AUC, positives vs rated negatives | 0.514 |
| Log loss | 0.667 |
| Accuracy / precision / recall / F1 | 0.587 / 0.571 / 0.555 / 0.563 |

Ranking, where each held-out positive is ranked among 99 recipes the user never
rated:

| Metric | Value |
| --- | --- |
| HR@10 | 0.197 |
| NDCG@10 | 0.092 |
| MRR | 0.086 |
| Mean rank | 38.0 of 100 |

HR@10 of 0.197 is about twice what random ordering gives (0.10). The spec
reports a popularity baseline of about 0.14 under the same negative sampling.
The AUC against rated negatives, 0.514, is close to chance: the model tells
recipes a user engages with apart from random ones, but hardly separates recipes
the user liked from recipes they rated and disliked.

### How the API uses it

[`api/src/remymy_api/inference/service.py`](api/src/remymy_api/inference/service.py)
wraps the pipeline in
[`model_inference_score_recommend.py`](api/src/remymy_api/inference/model_inference_score_recommend.py)
next to it.

At startup the API:

1. Creates the shared `recipe_repository`, which builds the catalog database
   (`CATALOG_DATABASE_PATH`) from `price_mapped_nutrients.csv` or adds the CSV's
   new foods to it, and reads every food from it: 10,658 FDC foods, each with a
   price (the sum of its FairPrice item prices, in SGD) and nutrients per 100 g.
   Missing nutrients take the catalog median.
2. Embeds every catalog food with the ItemEncoder and builds an HNSW index with
   cosine distance (M 16, ef 200). The index is saved under `INDEX_DIR` and
   reused while the model files and the catalog's foods are unchanged.
3. Has the repository build its ingredient index from the catalog
   (`recipe_repository.load_ingredient_index()`), which `digest` matches
   ingredient lines with. Each catalog food gets a key: the first
   comma-separated part of its FDC description, normalized by the repository's
   `name_key` (lowercase, parentheses and punctuation removed, stop words
   dropped, singularized), so "Waffles, buttermilk, frozen" becomes `waffle`.
   For each key it keeps one fully priced food, the one whose price is closest
   to the median price of that key's foods. This gives 1,381 keys. The method
   is the exact-match step of `map_recipe_nutrients.ipynb`.

For each recommendation (one at a time) it:

1. Builds the user's `History`: their `user_history` rows (`UserHistory`:
   recipe, like or not, timestamp), oldest first.
2. Digests each of its recipes with `recipe_repository.digest`: normalizes each
   ingredient line with `name_key` and looks it up in the ingredient index, a
   food once per recipe. The result is a `Recipe` holding the matched foods and
   the mean of their nutrients. A recipe in the history twice is digested once.
3. Turns every food of each entry's recipe into a history item with that
   entry's date and its like as a rating on the model's 0–5 scale: 5 liked, 0
   not (training counted 3 to 5 as liked).
4. Averages the history items' normalized embeddings into one query vector and
   retrieves the 100 nearest catalog foods, excluding the history itself.
5. Drops candidates with cosine similarity 0.97 or more to any history food,
   since the catalog holds many near-identical foods.
6. Scores the rest with the reranker.
7. Picks `top_k` by maximal marginal relevance, with weight 0.5 on P(like) and
   0.5 on diversity. It never picks two foods with cosine similarity 0.97 or
   more, so it can return fewer than `top_k`.

### Limitations

- **Recipes in training, single foods in serving.** The model was trained on
  whole Food.com recipes, but it retrieves and scores single catalog foods.
  Their numbers are on different scales: nutrients per 100 g instead of per
  serving, and one item's price instead of a recipe's total. Treat the scores
  as a ranking signal rather than calibrated probabilities.
- **Near-chance on liked vs disliked** (AUC 0.514 against rated negatives).
- **Low ingredient match coverage.** Matching is exact, and the recipe ingester
  leaves quantity fragments in ingredient names (for example ". kosher salt").
  On the local recipe database, 7,269 of the 13,489 recipes with ingredient
  lines match no priced food, and the best match about half of their lines. The
  demo user's recipes are picked from those that match best (21 of their 50
  lines). A user with no matched food gets no profile and no recommendations.
- **Arbitrary stand-in foods.** The median-price food stands for its whole key,
  so "tomatoes" maps to "Tomatoes, sun-dried" (258 kcal per 100 g), which skews
  the nutrient averages.

---

## The API

### Running it

- **Docker:** `docker compose --env-file .env.nonprod up --build` from this
  directory starts everything, including Redis. The API loads the model and
  builds the HNSW index before it reports healthy.
- **Locally:** `./run-local.sh` starts the Data API and waits for it to become
  healthy. Alternatively, run `python3 serve.py` in `api/`, which fills in
  localhost defaults (see [Configuration](#configuration)). Redis on
  `localhost:6379` (for example `redis-server`) is only the cache; without it
  the API runs the model on every request.
- **Swagger:** http://localhost:8002/docs. `/` redirects to `/docs`.
- **Demo user:** with `SEED_DEMO_USER=true` (the default in compose and in
  `serve.py`), creating the users database's schema also injects a `demo`
  user and its likes with SQL (`DEMO_SEED` in
  [`api/src/remymy_api/users.py`](api/src/remymy_api/users.py)). Its history
  is five real recipes of the 13k recipes CSV, by `recipe_id`: it liked Roast
  Chicken with Carrots, Baked Eggs with Tomato, Avocado and Watercress Salad and
  Meatballs and Noodle Soup (Almondigas), and not Citrus Cookies. Each is the
  recipe of its kind whose ingredient lines match the priced catalog
  best. Nothing is written once the demo user has a history, so an existing
  users database keeps its old one. In Swagger,
  `GET /api/recommend/{username}` comes pre-filled with `demo`.

### Data API endpoints (port 8002)

| Method | Path | Parameters | Returns |
| --- | --- | --- | --- |
| GET | `/health` | none | `{"status": "ok"}` while the process is up |
| GET | `/ready` | none | `{"status": "ok"}` once the recipe database is usable, otherwise 503 |
| GET | `/api/recipes` | `q` (optional, max 200 characters), `limit` 1–100 (default 20), `offset` ≥ 0 | Paged recipe summaries. `q` searches titles and ingredient lines, case-insensitive |
| GET | `/api/recipes/{recipe_id}` | none | Title, ingredient lines, instruction steps; 404 if unknown |
| GET | `/api/recommend/{username}` | `top_k` 1–50 (default 10) | Buying profile and recommended foods |
| GET | `/api/users/{username}` | none | The user's `id`, `username` and `preferences`; 404 if unknown |
| PUT | `/api/recipes/{recipe_id}/rating` | `user_id`, `like` (`true` or `false`) | Adds whether the user liked the recipe to their history, and returns that `UserHistory` row; 404 for an unknown user or recipe |

#### `GET /api/recommend/{username}`

```bash
curl "http://localhost:8002/api/recommend/demo?top_k=3"
```

For the demo user on the local recipe database, rounded:

```json
{
  "profile": {
    "user_id": 1,
    "avg_price": 6.53,
    "avg_nutrient_values": {
      "carb_g": 12.08,
      "cholesterol_mg": 41.76,
      "energy_kcal": 268.71,
      "fat_g": 24.33,
      "fiber_g": 1.49,
      "protein_g": 2.44,
      "satfat_g": 11.16,
      "sodium_mg": 2506.43,
      "sugars_g": 9.4
    }
  },
  "items": [
    {"id": "169286", "title": "Spinach, canned, regular pack, drained solids", "score": 0.795},
    {"id": "168916", "title": "Pasta, whole grain, 51% whole wheat, remaining unenriched semolina, cooked", "score": 0.784},
    {"id": "2709617", "title": "Spinach, canned, cooked, no added fat", "score": 0.782}
  ]
}
```

That profile rests on the 21 foods matched from the demo user's 50 ingredient
lines (see [Limitations](#limitations)).

- **`profile`:** averages over every catalog food matched from the user's
  history, counting a food once per history entry. `avg_price` is in SGD, and
  nutrients are per 100 g. Only nutrients the matched foods have are listed.
  It is `null` when no ingredient in the history matched a priced food.
- **`items`:** catalog foods, not recipes. `id` is the FDC id, `title` the FDC
  description, and `score` the model's P(like), highest first.
- **Unknown user, or no history:** `{"profile": null, "items": []}` with status
  200.
- **Errors:**
  - 503 `Recommendations are unavailable: the food catalog is not loaded.` when
    the API started with neither the catalog CSV nor the catalog database.
  - 503 `Recipe data is unavailable.` when the recipe database is missing.
  - 422 when `top_k` is out of range.

### The inference service

The route calls `Recommender.recommend(history, recipes, top_k, candidates=100)` in
[`service.py`](api/src/remymy_api/inference/service.py) in the same process,
one call at a time.

| Argument | Type | Meaning |
| --- | --- | --- |
| `history` | `History` | The user's `UserHistory` rows, oldest first |
| `history[].recipe_id` | string | The recipe; `recipes` holds its digested `Recipe` |
| `history[].like` | boolean | Goes to the model as rating 5 (liked) or 0 (not) |
| `history[].timestamp` | datetime | Its date orders the history and cuts the windows |
| `recipes` | `recipe_id` → `Recipe` | Each history recipe as `recipe_repository.digest` made it; its `ingredients` are the catalog foods its lines matched |
| `top_k` | integer | Recommendations kept; the route asks for 50 |
| `candidates` | integer | Foods retrieved before reranking, default 100 |

For this history:

```python
from datetime import datetime, timezone

from remymy_api.history import History
from remymy_api.repository.recipe_repository import recipe_repository
from remymy_api.schemas import UserHistory

soup = recipe_repository.digest("openrecipes:1", ["chicken breast", "tomatoes", "salt", "butter"])
apples = recipe_repository.digest("openrecipes:2", ["apples", "butter"])
history = History(user_id=1)
history.add(UserHistory(user_id=1, timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc), like=True,
                        recipe_id="openrecipes:1"))
history.add(UserHistory(user_id=1, timestamp=datetime(2026, 9, 10, tzinfo=timezone.utc), like=False,
                        recipe_id="openrecipes:2"))
items = recommender.recommend(history, {"openrecipes:1": soup, "openrecipes:2": apples}, top_k=5)
```

`soup.ingredients` holds four foods ("Chicken breast, NS as to cooking method,
skin eaten", "Tomatoes, sun-dried", "Salt, table" and "Butter, Clarified butter
(ghee)") and `apples.ingredients` the Fuji apples and the same butter.
`recommend` returns the scored items in MMR pick order. The first one:

```json
{"fdc_id": 2709611, "name": "Mustard greens, frozen, cooked, fat added", "price": 3.17,
 "p_like": 0.718, "similarity": 0.849}
```

`similarity` is the cosine similarity between the food and the user's query
vector. When no recipe in the history matched a food, `items` is empty.

### How a recommendation request is served

1. **Find the user id.** Look up `user:{username}` in Redis. If it is missing,
   query `app_user` in the users database and store the id in Redis for
   `CACHE_TTL_SECONDS` (default 86400, one day). New users are written to
   Redis when they are created. Unknown usernames are not cached.
2. **Load the history:** the user's newest 50 `user_history` rows.
3. **Check the cache** at `recommendations:{user_id}`. On a hit, return it,
   trimmed to `top_k`.
4. **On a miss:**
   1. Read the ingredient lines of each history recipe from the recipe database.
   2. Put the rows in the user's `History`, oldest first, and digest each of
      its recipes with `recipe_repository.digest`.
   3. Call the inference service with the `History`, the `Recipe`s and
      `top_k=50`.
   4. Compute the `UserBuyingProfile` from the foods of the `History`'s recipes.
   5. Store the result in Redis for `CACHE_TTL_SECONDS` (default 86400, one
      day), then return it trimmed to `top_k`.

Because the API always asks for 50 items, one cache entry serves any `top_k`.
A user's new history only affects their recommendations once the cached entry
expires. If Redis is down, user lookups fall back to the users database and
recommendations are still answered, running the model on every request.

### Data model

API schemas, in [`api/src/remymy_api/schemas.py`](api/src/remymy_api/schemas.py):

| Schema | Fields |
| --- | --- |
| `Preferences` | `special_diet` (`vegetarian`, `halal`, `Hinduism` or `null`), `cuisines` (up to 40), `preferred_nutrient` (up to 64 characters). Unknown keys are rejected |
| `User` | `id`, `username`, `preferences` |
| `UserHistory` | `user_id`, `timestamp`, `like` (boolean), `recipe_id` |
| `Ingredient` | `fdc_id`, `name`, `price`, `nutrient_values` |
| `UserBuyingProfile` | `user_id`, `avg_price`, `avg_nutrient_values` |
| `RecommendedRecipe` | `id`, `title`, `score` |
| `RecommendationResponse` | `profile` (`UserBuyingProfile` or `null`), `items` (list of `RecommendedRecipe`) |

What the recommend route hands the inference service, in
[`api/src/remymy_api/recipe.py`](api/src/remymy_api/recipe.py) and
[`api/src/remymy_api/history.py`](api/src/remymy_api/history.py):

| Class | Holds |
| --- | --- |
| `Recipe` | `recipe_id`, `ingredients` (the `Ingredient`s its lines matched), `nutrient_values` (their mean, per 100 g) |
| `History` | `user_id` and `history`, a queue of `UserHistory`, oldest first; past 50, each new entry drops the oldest |

Storage:

| Store | Location | Contents |
| --- | --- | --- |
| Recipe database | `data/remymy-food.db`, mounted read-only | `recipe`, `recipe_ingredient`, `recipe_instruction`, plus FDC Foundation Foods tables. Built by `api/migrations/bootstrap.py` and replaced on rebuild |
| Users database | `USER_DATABASE_PATH`, the `remymy-user-data` volume in compose | `app_user` (`user_id`, unique `username`, `preferences` as SQLite JSONB, `created_at`) and `user_history` (`user_id` → `app_user`, `timestamp`, `liked` 1 or 0, `recipe_id`). A database from before likes has its 0–5 ratings turned into likes on startup, 3 to 5 liked |
| Redis | `redis` service, 256 MB, least-recently-used eviction | `user:{username}` → user id; `recommendations:{user_id}` → cached response JSON |
| Catalog database | `CATALOG_DATABASE_PATH`, the `remymy-user-data` volume in compose | `price_mapped_nutrients`: the priced FDC foods, one row per `fdc_id`. `recipe_repository` creates it on startup from `data/price_mapped_nutrients.csv`, adds the CSV's new `fdc_id`s, and serves it to the inference service |

The users database enforces the preference rules itself: a `CHECK` constraint
holds `preferences` to exactly the three keys and their allowed values, and a
trigger rejects non-string cuisines. `user_history.recipe_id` points at
`recipe.recipe_id` in the recipe database. SQLite cannot enforce a foreign key
across files, so `add_history` checks it in code.

### Configuration

Data API:

| Variable | Docker default | `api/serve.py` default | Purpose |
| --- | --- | --- | --- |
| `DATABASE_PATH` | `/data/remymy-food.db` | `../data/remymy-food.db` | Recipe database |
| `USER_DATABASE_PATH` | `/state/remymy-users.db` | `./data/remymy-users.db` | Users database, created on startup |
| `CATALOG_PATH` | `/data/price_mapped_nutrients.csv` | `../data/price_mapped_nutrients.csv` | Priced FDC catalog CSV the catalog database is built from |
| `CATALOG_DATABASE_PATH` | `/state/remymy-catalog.db` | `./data/remymy-catalog.db` | Catalog database, created and updated on startup |
| `ARTIFACT_DIR` | `weights/gru_xattn_reranker` | same | Model weights and config |
| `INDEX_DIR` | `/var/cache/remymy/retrieval-index` | system temp folder | Where the HNSW index is saved |
| `REDIS_URL` | `redis://redis:6379/0` | `redis://localhost:6379/0` | Cache |
| `CACHE_TTL_SECONDS` | `86400` | `86400` | Lifetime of cached user ids and recommendations (one day) |
| `SEED_DEMO_USER` | `true` in compose | `true` | Inject the `demo` user and its ratings into the users database at startup |
| `LOG_LEVEL` | `INFO` | `INFO` | Level of the API's own log (`DEBUG` adds cache hits, ingredient matching and each pipeline step); compose passes it to `api-migrate`, `mcp` and `models` too |
| `HOST`, `PORT` | `0.0.0.0`, `8002` | `127.0.0.1`, `8002` | Listen address |

### Building the recipe database

`api/migrations/bootstrap.py` builds `data/remymy-food.db` from two sources:

- **`--recipes`:** the 13k Open Recipes dataset, as a `.csv` with `id`,
  `title`, `ingredients` and `instructions` columns, or as a `.db` with a
  `recipes` table of the same columns.
- **`--fdc-dir`:** a FoodData Central **Foundation Foods** CSV export. The full
  FDC export also works in principle, but its roughly 27 million nutrient rows
  make the build take hours and produce a database tens of GB in size.

In Docker, `api-migrate` runs this once, using `RECIPES_SOURCE` and
`FDC_SOURCE_DIR`, and leaves an existing valid database unchanged.

### Tests

```bash
cd api
PYTHONPATH=src python -m pytest
```

The suite uses a fake Redis, so it does not need Redis running. The route tests
stand a fake recommender in for the model; `tests/test_inference_service.py`
runs the real model and HNSW index over a small catalog.
