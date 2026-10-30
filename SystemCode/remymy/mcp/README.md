# Remymy MCP gateway

This is the small Python MCP service used by the Remymy web app. It exposes
three tools over stateless Streamable HTTP:

- `say_hello(name: str = "there")` — returns a friendly greeting.
- `recommend_meals(preferences, query, limit)` — filters the mounted Food.com
  recipe dataset, then ranks matches with the model service. The chat app uses
  this MCP tool for every connected meal recommendation.
- `rank_foodcom_meals(candidate_recipe_ids, user_id, content_scores, limit)` —
  calls the model service for a hybrid collaborative/content ranking. Callers
  must apply hard dietary and time constraints before invoking this tool.

`recommend_meals` screens diet constraints before model ranking: vegan recipes
must have the source vegan tag and pass an animal-ingredient check; vegetarian
recipes must be source-tagged and pass a meat/seafood check. Halal requests are
limited to vegan-tagged recipes and screened for animal ingredients, pork,
alcohol, and gelatin terms. The Food.com data has no halal certification, so
these are not certified halal meals; verify source ingredients and
cross-contamination.

The MCP endpoint is `http://localhost:8000/mcp` when the service is running
locally. The health endpoint is `http://localhost:8000/health`.

## Run locally

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Eval

### How to run
In `.env.nonprod`, set `JEV_API_KEY` to your JudgeEval API key. Then run:

```
cd SystemCode/remymy
docker compose exec mcp python -m eval.judge_eval --limit 1
docker compose exec mcp cat target/eval/jev_report.json
```

### 📊 Evaluation Metrics Reference

Our evaluation pipeline captures 11 metrics per chat trace, grouped into three categories: **Ranking Accuracy** (how well the model sorts the pool), **Output Quality** (how good the final selected meals are), and **System Constraints** (did we break any rules).

#### 1. Ranking Accuracy (Pool Metrics)
These metrics measure the quality of the `models` ranker. The Jev LLM judge grades every candidate in the pool from 0 to 3. We then check how well the model ordered them.
*Metadata source: `jev-judge-pool`*

*   **`nDCG@6` (Normalized Discounted Cumulative Gain):**
    The gold standard for ranking. It rewards the model for putting grade-3 meals at the very top of the list and heavily penalizes putting them lower down. A score of `1.0` is a perfect ranking; `0.0` means the ranking is worse than random.
*   **`Precision@6`:**
    Out of the top 6 meals shown, what percentage were actually good (grade ≥ 2)? If the top 6 contain two good meals, Precision is `2/6 = 0.333`.
*   **`Recall@6`:**
    Out of *all* the good meals in the entire candidate pool, what percentage made it into the top 6? (e.g., if the pool had 20 good meals and 1 made the top 6, Recall is `0.05`).
*   **`pairwise_accuracy`:**
    The model is shown two meals with different grades. How often does it correctly rank the better one higher? `1.0` means it always gets it right.

#### 2. Output Quality (Solver Metrics)
These metrics measure the final 3-day meal plan the agent actually outputted to the user, based on the Jev grades of the selected meals.
*Metadata source: `jev-judge-output`*

*   **`plan_mean_grade`:**
    The average Jev grade (0 to 3) of the final selected meals. A score of `1.5` means the agent selected meals that were, on average, slightly below "good" (grade 2).
*   **`plan_share_good`:**
    The percentage of the final selected meals that received a grade ≥ 2.
*   **`plan_repeat_rate`:**
    Did the agent recommend the same meal twice? `0.0` means zero repeats; `0.2` means 20% of the plan was duplicate meals.
*   **`diversity`:**
    The average pairwise dissimilarity (1 - Jaccard overlap) of the ingredients in the final meals. A score of `1.0` means no two meals share any ingredients; `0.0` means they all use the exact same ingredients.
*   **`coverage`:**
    Out of all the unique recipes shown across all traces, how many were actually selected in a plan?

#### 3. System Constraints (Filter Metrics)
These metrics use an *independent* screen (separate from the primary filter code) to catch the model slipping up.
*Metadata source: `jev-judge-output`*

*   **`violation_rate`:**
    The percentage of final selected meals that broke a hard constraint (e.g., a vegan user got a meal with dairy, or a 30-min limit user got a 60-min meal). **This should always be `0.0`.** If it's higher, your agent or ranker is ignoring the hard filters.
  *   **`over_filtering_rate`:**
      The percentage of user requests where the system could not find enough meals to fill the `limit` (e.g., returned fewer than 6 meals because the filters were too strict).

The Docker Compose app is configured to call the same endpoint at
`http://mcp:8000/mcp`.
