# Service prompt: models

## System prompt

You are reconstructing Remymy's lightweight Python model-serving service. Serve the trained Food.com hybrid ranker through FastAPI and NumPy, with deterministic cold-start behavior and legacy token decoding. Preserve numerical behavior and HTTP contracts; do not replace the trained model with an LLM or pretend a missing artifact is usable.

## System bound

- Own `SystemCode/remymy/models/`: ranking primitives, HTTP serving, token mapping, related training/evaluation utilities, and tests.
- Serve port 8001. MCP sends already-filtered candidate ids and content scores. The service neither retrieves recipes nor decides dietary/allergy/time eligibility from ingredients.
- Load a trusted local MODEL_ARTIFACT_PATH read-only; use TOKEN_VOCAB_PATH for full token decoding. Do not fetch or train artifacts on an HTTP request.
- Training utilities are offline; do not add a training daemon or access the application's guest database.

## System contract

### HTTP API

- `GET /` and `GET /health` load/access the cached ranker and return `{status:"ok",model_loaded:true,artifact:<configured path>}` when it loads. A missing/broken artifact must fail, not report a usable model.
- `POST /v1/rank` accepts strict JSON:
  - user_id: integer or null, default null.
  - candidates: 1–1000 objects with recipe_id integer, content_score [0,1] default 0, eligible boolean default true.
  - limit: 1–100, default 6.
  - collaborative_weight: [0,1], default 0.75.
  - filter_seen: boolean, default true.
- Response: `{user_id,recommendations,model:"bpr_matrix_factorization_hybrid_v1"}`.
- Each ranked item has recipe_id, score, collaborative_score, content_score, source (hybrid/cold_start), and reason_codes.
- `POST /v1/token-map` accepts token_ids as an integer list or Python-literal list string, with the existing 5000-length field bound. Parse strings using ast.literal_eval, never eval. Invalid list representation →422 `token_ids must be a list of integers`. Return token_ids/tokens/decoded.
- Reject extra request fields through Pydantic. Do not silently invent a new error envelope for artifact-loading failures.

### Ranking algorithm

- Load .npz arrays with allow_pickle=false: ids, latent factors, biases, popularity, global mean, optional score mode, and CSR-style seen-item indices.
- Exclude candidates with eligible=false. For a known user and trained item, honor filter_seen through its sorted seen indices.
- A warm item has an artifact item index and positive popularity. For known user + warm item, compute latent dot-product plus biases/global mean. With score_mode=1 use sigmoid with raw score clipped to [-20,20]; otherwise clip (raw-1)/4 into [0,1].
- Warm/known-user final score = collaborative_weight × collaborative_score + (1-collaborative_weight) × content_score; source hybrid.
- Otherwise final score = 0.75 × content_score + 0.25 × popularity; source cold_start. An unknown item has zero popularity/collaborative score.
- Clamp content to [0,1]. Round exposed score components to six decimals. Sort by descending final score, then ascending recipe_id; return at most limit.
- Scores are ranking signals, not safety or calibrated preference guarantees.
- Preserve the distinction between explicitly ineligible flags and actual safety screening: the caller supplies eligibility.

### Token decoding and operations

- Decode the legacy OpenAI-GPT vocabulary used by Food.com's PP_recipes.csv, not GPT-2/tiktoken.
- Special ids: 40478 </s>, 40479 </p>, 40480 <R>, 40481 </R>, 40482 <s>. Unknown ids become <unknown:id>.
- Text decoding removes document/start/padding controls, converts step endings to sentence separators, and replaces </w> with spaces.
- Without a vocabulary file, special tokens/unknown markers still work; do not claim full ordinary-word decoding.
- Cache ranker and token mapper once per process. LOG_LEVEL controls standard timestamped logging.
- Compose uses the recommender profile, mounts /data read-only, defaults MODEL_FILE to hybrid-foodcom-bpr.npz, and waits for models-vocab completion before model health checks.

### Offline reproduction boundary

Retain prepare_cf_splits.py, train.py, evaluate_hybrid.py and their current CLI contracts. Collaborative evaluation uses per-user chronological warm-item splits, not the original disjoint-item generation splits. Do not present sampled-negative experiment metrics as full-catalog production performance.

## Build prompt

Implement the FastAPI wrapper and HybridRanker/TokenMapper behavior above in the existing models directory. Reuse trusted local artifacts, preserve the pure numerical tests, expose port 8001, and keep environment-driven artifact/vocabulary configuration. Do not implement the API's separate window-Transformer recommender here.

## Acceptance checks

- From `SystemCode/remymy/models`: `python -m pytest`.
- Cover warm-user/item scoring, unknown users/items, no-interaction items, seen/ineligible filtering, stable ties, limits, score modes, and token special/unknown/literal decoding.
- Test strict request bounds and health with a real or fixture artifact. Missing artifacts are a prerequisite failure, not a passed health check.
- Report checks run and distinguish synthetic artifact tests from the real trained model.
