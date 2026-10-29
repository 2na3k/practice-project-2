import ast
import csv
import gc
import heapq
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from langfuse import observe, get_client
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from eval.evaluation import save_evaluation
from eval.review_api import grades_post, review_get
from features.screens import (
    DAIRY_TERMS, EGG_TERMS, HALAL_BLOCKED_TERMS, MEAT_TERMS,
    SEAFOOD_TERMS, VEGAN_EXTRA_TERMS, has_any_term, normalize,
)
from core.config import pool_size

# One line per record with time, level and module, as in the Data API, at the level LOG_LEVEL names.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
logging.basicConfig(
    level=LOG_LEVEL if isinstance(logging.getLevelName(LOG_LEVEL), int) else "INFO",
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("remymy.mcp")

mcp = MCPServer("Remymy MCP")

# The maximum number of candidates to retrieve locally before sending to the ranker.
# This determines the evaluation pool size.
POOL_SIZE = pool_size()

CUISINE_TAGS = {"italian", "mexican", "chinese", "japanese", "korean", "thai", "indian", "greek", "french", "mediterranean", "middle eastern", "american", "canadian"}
HIDDEN_TAGS = {"time-to-make", "course", "main-ingredient", "preparation", "occasion", "dietary"}

_catalog_lock = threading.Lock()
_catalog_rows: list["Recipe"] | None = None
_catalog_ready = threading.Event()

_cols: dict[str, int] = {}

class Recipe:
    __slots__ = ("id", "minutes", "tags", "ings", "title", "description", "calories", "protein_dv", "offset")
    def __init__(self, id, minutes, tags, ings, title, description, calories, protein_dv, offset):
        self.id, self.minutes, self.tags, self.ings = id, minutes, tags, ings
        self.title, self.description = title, description
        self.calories, self.protein_dv, self.offset = calories, protein_dv, offset

class _Lines:
    """Feeds csv.reader from a binary file and tracks the byte offset of the next line."""
    def __init__(self, handle):
        self.handle, self.pos = handle, 0
    def __iter__(self):
        return self
    def __next__(self):
        line = self.handle.readline()
        if not line:
            raise StopIteration
        self.pos += len(line)
        return line.decode("utf-8")

def _load_catalog(path: Path) -> list[Recipe]:
    global _catalog_rows
    with _catalog_lock:
        if _catalog_rows is not None:
            return _catalog_rows
        started = time.perf_counter()
        rows: list[Recipe] = []
        gc.disable()  # millions of allocations; no point scanning them while loading
        try:
            with path.open("rb") as handle:
                lines = _Lines(handle)
                reader = csv.reader(lines)
                _cols.update({name: i for i, name in enumerate(next(reader))})
                c_id, c_min, c_tags, c_nut, c_ing, c_name, c_desc = (
                    _cols[k] for k in ("id", "minutes", "tags", "nutrition", "ingredients", "name", "description"))
                while True:
                    offset = lines.pos          # byte offset where this record starts
                    try:
                        rec = next(reader)
                    except StopIteration:
                        break
                    try:
                        recipe_id, minutes = int(rec[c_id]), int(rec[c_min])
                        tags = tuple(sys.intern(normalize(t)) for t in ast.literal_eval(rec[c_tags]))
                        ings = tuple(sys.intern(normalize(str(i))) for i in ast.literal_eval(rec[c_ing]))
                        nutrition = [float(x) for x in rec[c_nut].strip("[] ").split(",")]
                    except (ValueError, TypeError, SyntaxError, IndexError):
                        continue
                    calories, protein_dv = (nutrition[0], nutrition[4]) if len(nutrition) > 4 else (None, None)
                    rows.append(Recipe(recipe_id, minutes, tags, ings, rec[c_name].strip(),
                                       rec[c_desc].strip(), calories, protein_dv, offset))
        finally:
            gc.enable()
        gc.collect()
        gc.freeze()  # long-lived catalog is excluded from future GC passes
        _catalog_rows = rows
        logger.info("Loaded %d recipes in %.1fs.", len(rows), time.perf_counter() - started)
        _catalog_ready.set()
        return rows

def _hydrate(path: Path, recipes: list[Recipe]) -> dict[int, tuple[list, list, list]]:
    """Re-read tags, ingredients and steps from the CSV for just the returned recipes."""
    c_tags, c_ing, c_steps = _cols["tags"], _cols["ingredients"], _cols["steps"]
    out = {}
    with path.open("rb") as handle:
        for r in recipes:
            handle.seek(r.offset)
            rec = next(csv.reader(_Lines(handle)))
            try:
                out[r.id] = (list(ast.literal_eval(rec[c_tags])),
                             [str(i) for i in ast.literal_eval(rec[c_ing])],
                             [str(s) for s in ast.literal_eval(rec[c_steps])])
            except (ValueError, SyntaxError, IndexError):
                continue
    return out

def _build_meal(row: Recipe, raw_tags: list, ingredients: list, steps: list) -> dict:
    tags = [normalize(t) for t in raw_tags]
    recipe_tags = [t.replace("-", " ") for t in tags if t not in HIDDEN_TAGS][:8]
    cuisine = next((t.replace("-", " ").title() for t in tags if t in CUISINE_TAGS), "Global")
    return {
        "id": f"foodcom:{row.id}",
        "title": row.title,
        "description": row.description[:200],
        "cuisine": cuisine,
        "minutes": row.minutes,
        "calories": round(row.calories) if row.calories is not None else 0,
        "protein": round(row.protein_dv / 2) if row.protein_dv is not None else 0,
        "tags": recipe_tags,
        "ingredients": ingredients[:20],
        "steps": steps[:20],
        "image": "",
        "color": ["#dcefe7", "#f8e5c2", "#f4d8c5", "#f5e8bd"][row.id % 4],
    }

@mcp.tool()
def say_hello(name: str = "there") -> str:
    """Say hello to someone."""
    clean_name = name.strip() or "there"
    logger.debug("say_hello for %r.", clean_name)
    return f"Hello, {clean_name}! Welcome to Remymy."


@mcp.tool()
@observe(name="recommend_meals.pipeline")
def recommend_meals(preferences: dict, query: str = "", limit: int = 6) -> str:
    """Find real Food.com recipes, enforce diet/time exclusions, then model-rank them."""

    if not isinstance(preferences, dict) or not isinstance(query, str) or len(query) > 2000:
        raise ValueError("invalid recommendation input")

    if not _catalog_ready.wait(timeout=10):
        raise RuntimeError("Recipe catalog is still loading; try again shortly.")

    diet = preferences.get("diet", "any")
    max_minutes = preferences.get("maxMinutes", 30)
    goal = preferences.get("goal", "balanced")
    exclusions = preferences.get("exclude", [])
    requested_cuisine_raw = str(preferences.get("cuisine", "any"))
    if diet not in {"any", "vegetarian", "vegan", "halal"}:
        raise ValueError("invalid diet preference")
    if not isinstance(max_minutes, (int, float)) or not 1 <= max_minutes <= 90:
        raise ValueError("invalid time preference")
    if goal not in {"balanced", "protein", "light"} or not isinstance(exclusions, list) or len(exclusions) > 24 or not all(isinstance(item, str) and len(item) <= 80 for item in exclusions):
        raise ValueError("invalid meal preferences")

    catalog = Path(os.getenv("RECIPE_CATALOG_PATH", Path(__file__).resolve().parent.parent / "data/raw/food-com/RAW_recipes.csv"))
    if not catalog.is_file():
        raise RuntimeError(f"Recipe catalog is missing: {catalog}")

    requested_cuisine = normalize(requested_cuisine_raw)
    if not isinstance(limit, int) or not 1 <= limit <= 12:
        raise ValueError("limit must be between 1 and 12")
    stop_words = {"the", "and", "with", "for", "something", "meal", "meals", "food", "please", "suggest", "recommend", "ideas", "some", "under", "within", "minutes", "minute", "vegan", "vegetarian", "halal", "recipe", "recipes"}
    tokens = [word for word in normalize(query).split() if len(word) > 2 and word not in stop_words]
    exclusion_terms = set()
    aliases = {"meat": set(MEAT_TERMS), "dairy": set(DAIRY_TERMS), "seafood": set(SEAFOOD_TERMS)}
    for exclusion in exclusions:
        normalized = normalize(exclusion)
        exclusion_terms.update(aliases.get(normalized, {normalized}))
    halal_requested = diet == "halal" or "halal" in {normalize(item) for item in exclusions} or "halal" in normalize(query).split()
    exclusion_terms.discard("halal")

    candidates = []
    meal_courses = {"main dish", "lunch", "dinner"}
    if "breakfast" in tokens:
        meal_courses.add("breakfast")

    for row in _load_catalog(catalog):
        if row.minutes < 1 or row.minutes > max_minutes:
            continue

        tags = row.tags
        tagged_vegetarian = bool({"vegetarian", "vegan"}.intersection(tags))
        if diet == "vegetarian" and not tagged_vegetarian:
            continue
        if (diet == "vegan" or halal_requested) and "vegan" not in tags:
            continue
        if (diet == "vegan" or halal_requested) and has_any_term(row.ings, (*MEAT_TERMS, *SEAFOOD_TERMS, *DAIRY_TERMS, *EGG_TERMS, *VEGAN_EXTRA_TERMS)):
            continue
        if (diet == "vegetarian" or halal_requested) and has_any_term(row.ings, (*MEAT_TERMS, *SEAFOOD_TERMS)):
            continue
        if halal_requested and has_any_term(row.ings, HALAL_BLOCKED_TERMS):
            continue
        if not meal_courses.intersection(tags):
            continue

        ingredient_text = " ".join(row.ings)
        tag_text = " ".join(tags)

        if any(_term_re(term).search(ingredient_text) for term in exclusion_terms if term):
            continue
        if requested_cuisine != "any" and requested_cuisine not in tag_text:
            continue

        searchable = normalize(" ".join([row.title, row.description, ingredient_text, tag_text]))
        matched = sum(token in searchable for token in tokens)
        content_score = matched / len(tokens) if tokens else 0.5

        if goal == "light" and row.calories is not None:
            content_score = 0.7 * content_score + 0.3 * max(0.0, 1 - row.calories / 1500)
        elif goal == "protein" and row.protein_dv is not None:
            content_score = 0.7 * content_score + 0.3 * min(row.protein_dv / 100, 1.0)
        content_score = max(0.0, min(1.0, content_score))
        if tokens and not matched:
            continue

        entry = (content_score, -row.id, row.id, row)
        if len(candidates) < POOL_SIZE:
            heapq.heappush(candidates, entry)
        elif entry > candidates[0]:
            heapq.heapreplace(candidates, entry)

    if not candidates:
        return "[]"

    candidates.sort(key=lambda item: (-item[0], item[2]))
    scores = {rid: score for score, _, rid, _ in candidates}
    recipes = {rid: r for _, _, rid, r in candidates}
    rank_limit = min(max(POOL_SIZE, limit), 100)
    ranked = rank_foodcom_meals(list(recipes), content_scores=[scores[rid] for rid in recipes], limit=rank_limit)
    ranked_pool = [item for item in ranked["recommendations"] if item["recipe_id"] in recipes]
    top = ranked_pool[:limit]

    snapshot_pool = [{
        "recipe_id": str(item["recipe_id"]),
        "title": recipes[item["recipe_id"]].title,
        "content_score": item.get("content_score", scores[item["recipe_id"]]),
        "popularity": item.get("popularity", item.get("collaborative_score")),
        "source": item.get("source"),
    } for item in ranked_pool]
    plan_id = str(uuid4())
    save_evaluation(
        get_client().get_current_trace_id(), plan_id,
        {"preferences": preferences, "query": query, "limit": limit}, snapshot_pool, ranked_pool,
    )

    details = _hydrate(catalog, [recipes[item["recipe_id"]] for item in top])
    recommendations = []
    for item in top:
        row = recipes[item["recipe_id"]]
        if row.id not in details:
            continue
        meal = _build_meal(row, *details[row.id])
        reasons = ["Returned by Remymy's Food.com MCP recommender.", f"{meal['minutes']} min, within your {max_minutes}-minute limit."]
        if diet == "vegan":
            reasons.append("Passed the source vegan tag and ingredient screen; verify the source recipe for omissions.")
        elif diet == "vegetarian":
            reasons.append("Tagged vegetarian by the source catalog and screened for meat and seafood ingredients.")
        if halal_requested:
            reasons.append("Passed vegan-tag and listed-ingredient screens; not halal-certified. Verify source ingredients and cross-contamination.")
        recommendations.append({"meal": meal, "score": item["score"], "reasons": reasons})

    lf = get_client()
    lf.update_current_span(metadata={
        "diet": diet, "goal": goal, "max_minutes": max_minutes,
        "cuisine": requested_cuisine, "tokens": len(tokens),
        "candidates": len(candidates),
    })
    return json.dumps(recommendations)


@mcp.tool()
@observe(name="rank_foodcom_meals")
def rank_foodcom_meals(
        candidate_recipe_ids: list[int],
        user_id: int | None = None,
        content_scores: list[float] | None = None,
        limit: int = 6,
) -> dict:
    """Rank already-filtered Food.com candidates with the hybrid model."""
    if not candidate_recipe_ids or len(candidate_recipe_ids) > 1000:
        raise ValueError("candidate_recipe_ids must contain 1 to 1000 values")
    scores = content_scores or [0.0] * len(candidate_recipe_ids)
    if len(scores) != len(candidate_recipe_ids):
        raise ValueError("content_scores must align with candidate_recipe_ids")
    if any(score < 0.0 or score > 1.0 for score in scores):
        raise ValueError("content_scores must be between 0 and 1")
    payload = {
        "user_id": user_id,
        "limit": max(1, min(int(limit), 100)),
        "collaborative_weight": float(os.getenv("REMYMY_COLLABORATIVE_WEIGHT", "0.75")),
        "filter_seen": True,
        "candidates": [
            {"recipe_id": int(recipe_id), "content_score": float(score), "eligible": True}
            for recipe_id, score in zip(candidate_recipe_ids, scores, strict=True)
        ],
    }
    endpoint = os.getenv("MODEL_URL", "http://localhost:8001").rstrip("/") + "/v1/rank"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            ranked = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        logger.warning("Ranking %d candidates for user %s at %s failed: %s", len(candidate_recipe_ids), user_id, endpoint, exc)
        raise RuntimeError("Remymy's ranking service is unavailable") from exc
    logger.info("Ranked %d candidates for user %s in %.2fs.", len(candidate_recipe_ids), user_id, time.perf_counter() - started)
    return ranked


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/eval/{id}", methods=["GET"])
async def evaluation_review(request: Request) -> JSONResponse:
    return await review_get(request)


@mcp.custom_route("/eval/{id}/grades", methods=["POST"])
async def evaluation_grades(request: Request) -> JSONResponse:
    return await grades_post(request)


if __name__ == "__main__":
    catalog_path = Path(os.getenv("RECIPE_CATALOG_PATH", Path(__file__).resolve().parent.parent / "data/raw/food-com/RAW_recipes.csv"))

    # Start the CSV parsing in a background thread so the HTTP server can boot immediately
    threading.Thread(
        target=_load_catalog,
        args=(catalog_path,),
        daemon=True,
        name="catalog-warmup",
    ).start()

    logger.info("Starting the MCP service on port %s; ranking through %s.", os.getenv("PORT", "8000"), os.getenv("MODEL_URL", "http://localhost:8001"))
    mcp.run(
        transport="streamable-http",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )
