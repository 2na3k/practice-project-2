# Service prompt: api

## System prompt

You are reconstructing Remymy's FastAPI Data API. Keep the API as the sole application-data boundary, backed by unified SQLite and request-scoped SQLAlchemy repositories. Preserve its REST DTOs, guest product state, optional cache, and in-process recommendation/classification/clustering services. Prefer the runtime code and tests over outdated README descriptions.

## System bound

- Own `SystemCode/remymy/api/src/remymy_api/`, its tests, serving entrypoint, evaluation helpers, and Docker image. Share schema code with `api-migrate`; do not duplicate it.
- Own product persistence at `DATABASE_PATH` (Compose `/state/remymy.db`). Redis db 1 is optional caching, not the source of truth.
- Serve the app via REST on port 8002. Do not call MCP or the external `models` service for this API's personalized recommender; that is a separate chat path.
- Do not expose arbitrary SQL, raw import/quarantine payloads, internal database errors, or local paths through recipe endpoints.
- This service has guest users, not an implemented production authentication layer. User ids/usernames supplied to public routes are not proof of ownership.

## System contract

### Persistence and startup

- SQLite 3.45+ is required for JSONB preferences. Keep recipe, ingredient, instruction, tag, priced catalog, user, rating/history, saves, user-recipe metadata, and impression state in one database.
- Preserve recipe kinds `openrecipes`, `catalog`, `user`; generated user ids use `user-recipe:<n>`. Do not confuse raw Food.com ids, `foodcom:<id>` chat ids, numeric catalog ids, and namespaced recipe ids.
- Initialize missing tables and migrate legacy food/users data only when required. Keep foreign keys, preference CHECKs/triggers, tag provenance, and transactional writes.
- Catalog import inserts missing fdc_ids from `RECIPE_PATH`, leaving stored catalog rows unchanged. Fully priced Food.com records can join the catalog from `FOODCOM_RECIPE_PATH`, using the existing numeric offset. Preserve source strings and nutrient units.
- Database startup completes before serving. Term extraction, recommendation, clustering, and classification models load independently in a background thread. One model failure must not block unrelated routes.
- `GET /health` → `{"status":"ok"}` for liveness. `GET /ready` → same only if normalized recipe data is usable, otherwise 503. Readiness does not imply models are loaded.
- `GET /` redirects to `/docs`.

### REST surface

| Method | Endpoint                                  | Contract                                                                      |
| ------ | ----------------------------------------- | ----------------------------------------------------------------------------- |
| GET    | `/api/recipes`                            | q ≤200 chars, limit 1–100 default 20, offset ≥0; `{items,total,limit,offset}` |
| GET    | `/api/recipes/{recipe_id}`                | id/title/ingredients/instructions/quality_status/tags; 404 unknown            |
| POST   | `/api/recipes`                            | create user recipe, 201; validate owner exists                                |
| GET    | `/api/recipe/{fdc_id}`                    | priced catalog item, pack prices/sizes, per-100g nutrients, tags              |
| GET    | `/api/recommend/{username}`               | top_k 1–50 default 10; `{profile,items,source}`                               |
| POST   | `/api/guests`                             | 201 user with `guest-<32 hex digits>` username                                |
| GET    | `/api/users/{username}`                   | id/username/preferences; 404 unknown                                          |
| PUT    | `/api/users/{username}/set-preferences`   | replace preferences; return user; invalidate cached picks                     |
| GET    | `/api/users/{username}/history`           | newest first; limit 1–200 default 50                                          |
| GET    | `/api/users/{username}/likes`             | item-id → latest boolean like                                                 |
| GET    | `/api/users/{username}/recipes`           | full user-authored recipes, newest first                                      |
| GET    | `/api/users/{username}/saved`             | newest-first saved items                                                      |
| PUT    | `/api/users/{username}/saved/{recipe_id}` | idempotent save of known recipe/catalog/user item                             |
| DELETE | `/api/users/{username}/saved/{recipe_id}` | idempotent removal, 204                                                       |
| PUT    | `/api/recipes/{recipe_id}/rating`         | query user_id and like; validate both entities, return UserRating             |
| POST   | `/api/users/{username}/impressions`       | ordered recipe_ids and optional trace_id; 201 `{event_id}`                    |
| POST   | `/api/classify`                           | classify description with optional nutrients and extracted-term overrides     |
| GET    | `/api/classify/{fdc_id}`                  | training-food classification plus training labels                             |
| POST   | `/api/cluster`                            | `{item: RecipeDetail}`, query k 1–50 default 5; `{near_items:[Recipe]}`       |

- Recipe lists/details include imported and user recipes, not catalog placeholder rows. Exclude blank titles. Search title and raw/normalized ingredient strings case-insensitively with parameterized queries; previews contain at most three ingredients. Keep line ordering.
- Strict Pydantic DTOs reject extra keys where specified. Preferences: special_diet vegetarian/halal/Hinduism/null; cuisines ≤40; preferred_nutrient ≤64 chars; budget; at most 12 allowed allergen codes. Null fields take defaults. This is not the chat preference shape.
- User recipe: title 1–200 chars; 1–100 ingredient lines and ≤100 instruction lines, each 1–500 chars; ≤30 distinct snake_case tags with classifier/user source; recognized nutrient keys only. Return id/user_id/title/ingredients/instructions/tags/nutrients/created_at.
- Rating an item again updates its existing history rating rather than appending a duplicate. Preserve the implemented timestamp behavior.
- Keep missing entity 404, validation 422, and generic data/model-unavailable 503 responses. Log internal causes server-side. Do not infer that every exception path is already sanitized.

### API personalization — distinct from MCP ranking

- Before more than five rated items, return uncached popular eligible items with `source:"popular"` and score 0; popularity counts users who rated the item, not only likes. Exclude already rated items and use user-seeded stable tie ordering.
- After the threshold, build oldest-first history from the newest 50 ratings. Use the in-process retrieval/reranker, not the BPR HTTP service.
- Digest unknown-to-pipeline recipes into matched priced foods; pipeline-held recipes/foods stay as themselves. Map likes/dislikes to 5/0, preserve dates, and compute an optional buying profile from matched foods.
- Preserve the existing retrieval pipeline: interest clustering, eligible diet/allergy/budget routing, masked HNSW or exact search for sparse allowed sets, near-duplicate suppression, window Transformer/cross-attention reranking, and diversity selection. Do not invent new model weights or claim calibrated medical probabilities.
- Route output items are id/title/score; source is model/popular, profile is nullable. Unknown user returns profile null and no items with 200. Missing required catalog/recommender returns 503; some internal inference failures currently degrade to empty picks.
- Cache up to 50 model picks for any requested top_k. Default TTL 86400 seconds. Key families: user:{username}, recommendations:{user_id}, number_of_ratings:{user_id}.
- More than five counted ratings since caching invalidates picks and resets the counter. Preference changes also invalidate. Immediate dislike removal is currently disabled in the route; preserve/document rather than claim it is active.
- Redis read/write/capacity failures fall back to SQLite/recomputation. Do not require Redis availability for product correctness.

### Classification and clustering

- Classification query 1–500 chars; explicit nutrient values override parsed ones; missing nutrients use training medians. Optional product/adj/verb term lists are ≤20 each. Bare FDC ids use the lookup.
- Return description, terms, nutrients, labels, all scores, score_kind, model, known_terms, and optional training labels. `generic` is exclusive.
- Unknown training food →404; empty food description →422; missing/unloaded model or lookup →503.
- Clustering uses the trained bundle and catalog-available neighbors, excluding the query item. Preserve singleton warmup and required extractor/encoder synchronization.
- The clustering joblib artifact is not checked in. Missing artifacts must not stop recipe browsing. The current Dockerfile installs both requirement files.

## Build prompt

Reconstruct this service using FastAPI, Pydantic, SQLAlchemy, SQLite, existing inference modules, and Redis only as a cache. Keep all endpoints and DTOs above, shared schema migrations, background model loading, tests, logging, and Docker port 8002. Depend on completed api-migrate and started Redis. Mount /data read-only, remymy-user-data at /state, and model cache under /var/cache/remymy/models. Do not introduce PostgreSQL for application records or merge the two recommendation systems.

## Acceptance checks

- From `SystemCode/remymy/api`: `PYTHONPATH=src python -m pytest`.
- Cover liveness vs recipe readiness vs model availability; search/order/paging; unknown entities; malformed DTOs; rating upsert; saves; preferences; user recipes; impressions.
- Check cold-start threshold, cache failure/expiry/invalidation, model-unavailable behavior, catalog/recipe id distinctions, and migration preservation.
- Use fake Redis and mocked models for boundary tests; identify any real artifact tests or missing artifact prerequisites separately.
- Report files changed, checks run, and unresolved limits. Never reset a user's database to satisfy a test.

## Source anchors

- `SystemCode/remymy/docker-compose.yml`
