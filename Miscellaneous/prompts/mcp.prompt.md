# Service prompt: mcp

## System prompt

You are reconstructing Remymy's Python MCP recommendation gateway. Implement real Food.com candidate retrieval, deterministic pre-ranking screens, model-service delegation, and optional trace/evaluation capture. Use the existing MCP protocol/library and tests. Never substitute invented meals when a dependency fails.

## System bound

- Own `SystemCode/remymy/mcp/`: MCP tools, catalog loader, screening helpers, model HTTP client, evaluation modules, and review routes.
- Accept Streamable HTTP MCP requests on port 8000 at `/mcp`, stateless HTTP with JSON responses. App-side Pi tools are the primary caller.
- Read Food.com CSV through `RECIPE_CATALOG_PATH`; call `MODEL_URL/v1/rank` (Compose http://models:8001). Do not own the application SQLite database, guest sessions, or model training.
- Hard screens happen before ranking. Raw rank_foodcom_meals callers must already have applied eligibility constraints.
- Source ingredient data can be incomplete. Screening is not allergy assurance, halal certification, or medical advice.

## System contract

### MCP tools

1. `say_hello(name: str = "there") -> str`: trim name, use there if blank, return `Hello, {name}! Welcome to Remymy.`
2. `recommend_meals(preferences: dict, query: str = "", limit: int = 6) -> str`: return a JSON-encoded list of `{meal,score,reasons}`. No matches returns the string `"[]"`.
3. `rank_foodcom_meals(candidate_recipe_ids: list[int], user_id: int | None = None, content_scores: list[float] | None = None, limit: int = 6) -> dict`: return the model service's ranking object.

### Catalog retrieval and screens

- Start catalog warmup on a background thread. The health endpoint reports process liveness, not catalog readiness; recommendation calls wait up to 10 seconds for catalog readiness before reporting still-loading.
- Parse raw Food.com CSV columns id/minutes/tags/nutrition/ingredients/name/description/steps. Use safe literal parsing, skip malformed records, cache compact rows, and retain byte offsets to hydrate selected recipes.
- preferences.diet is any/vegetarian/vegan/halal; maxMinutes defaults 30 and must be 1–90; goal is balanced/protein/light; exclude is ≤24 strings of ≤80 chars; cuisine defaults any. Query is ≤2000 chars and limit is integer 1–12.
- Admit recipes with positive minutes within the limit and main dish/lunch/dinner tags; allow breakfast course when the query requests it.
- Vegetarian requires a vegetarian/vegan source tag plus no listed meat/seafood terms.
- Vegan requires a vegan source tag plus no meat/seafood/dairy/egg/other blocked animal terms.
- A halal request can come from diet, the halal exclusion marker, or query text. Require the vegan tag and screen listed animal ingredients plus pork/alcohol/gelatin-related terms. Do not claim the results are certified halal.
- Expand meat/dairy/seafood exclusion aliases, use the existing word/phrase-boundary screens, and require requested cuisine when not any.
- Compute query token-match content scores with existing normalization/stop words. With no query tokens use 0.5; with tokens require at least one match. Apply light/protein goal boosts as implemented and clamp scores to [0,1].
- Keep a bounded top candidate heap, default REMYMY_POOL_SIZE=500, with deterministic content-score/id tie ordering.
- Rank at most 100 returned pool entries through the model, then take the requested output limit. Hydrate only selected recipes; skip unhydratable records.

### Ranker delegation

- Validate 1–1000 candidate ids, aligned content scores in [0,1], and bound limit to 1–100.
- Send user_id, limit, collaborative_weight (REMYMY_COLLABORATIVE_WEIGHT, default 0.75), filter_seen=true, and candidates with recipe_id/content_score/eligible=true.
- Use a 10-second model HTTP timeout. On URL/JSON failure raise `Remymy's ranking service is unavailable`; do not silently fabricate a ranking.
- Ordinary recommend_meals currently supplies no user_id, so the ranker uses its content/popularity fallback rather than learned guest personalization.

### Output and observability

- Each meal contains id `foodcom:<raw_id>`, title, description capped at 200 chars, inferred cuisine, source minutes/calories/protein estimate, up to eight display tags, at most 20 ingredients and 20 steps, blank image, and deterministic card color.
- Reasons identify MCP provenance and time compliance. Vegan/vegetarian/halal reasons explain the screening actually done; halal reasons explicitly say not certified and advise source/cross-contamination verification.
- Do not convert model ranking scores into calibrated nutrition or safety confidence.
- `GET /health` returns `{"status":"ok"}`.
- Optional Langfuse tracing records recommend_meals.pipeline and rank_foodcom_meals.
- Optional sampled SQLite evaluation snapshots use REMYMY_EVAL_ENABLED/SAMPLE_RATE, REMYMY_EVALUATION_DB, and ranking/data version metadata; persist under /app/target on remymy-mcp-data.
- `GET /eval/{id}` and `POST /eval/{id}/grades` require a constant-time-checked Bearer REMYMY_EVALUATION_TOKEN. No token configured →503; invalid token →401; unknown snapshot →404.
- Blind review hides ranking/scores/source/prior grades and randomizes candidates. Grades map candidate ids to integer 0–3; reject invalid bodies or out-of-pool ids.
- Preserve existing offline evaluation/judge modules and explicit JEV configuration. Do not make judge access mandatory for recommendations.

### Known limits

- Separate budget/allergies fields are not implemented screens here. Do not describe them as enforced, even if the app retains them.
- Process health does not guarantee the CSV is loaded; the model health dependency does not prove a later request cannot fail.
- The catalog can contain incomplete ingredient/nutrition data. Hydrated output truncation is display behavior, not proof that a displayed ingredient list is exhaustive.

## Build prompt

Reconstruct the service with the three MCP tools, compact CSV cache, hard screens, bounded retrieval, model HTTP client, truthful source-grounded output, health route, and optional evaluation/reviewer facilities. Run it with the recommender Compose profile; depend on healthy models and mount RAW_recipes.csv read-only. Keep environment-driven endpoints and secrets. Do not add an LLM inside this service or a fallback recipe fixture in production.

## Acceptance checks

- From `SystemCode/remymy/mcp`: `python -m pytest`.
- Test vegetarian/vegan/halal edge cases, exclusion aliases, time/cuisine constraints, malformed inputs, no-match output, ranker payload/timeout/failure, and stable top-pool behavior.
- Verify source ids, hydration, output caps, and explicit non-certification language.
- Test evaluation disable/sampling, bearer-token checks, blinded review, and grade validation independently of live Langfuse/JEV.
- Report tests actually run and missing CSV/artifact/remote-service prerequisites.
