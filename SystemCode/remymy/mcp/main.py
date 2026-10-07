import ast
import csv
import heapq
import json
import os
import re
import unicodedata
import urllib.error
import urllib.request

from pathlib import Path

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse


mcp = MCPServer("Remymy MCP")


@mcp.tool()
def say_hello(name: str = "there") -> str:
    """Say hello to someone."""
    clean_name = name.strip() or "there"
    return f"Hello, {clean_name}! Welcome to Remymy."


@mcp.tool()
def recommend_meals(preferences: dict, query: str = "", limit: int = 6) -> str:
    """Find real Food.com recipes, enforce diet/time exclusions, then model-rank them."""

    if not isinstance(preferences, dict) or not isinstance(query, str) or len(query) > 2000:
        raise ValueError("invalid recommendation input")
    diet = preferences.get("diet", "any")
    max_minutes = preferences.get("maxMinutes", 30)
    goal = preferences.get("goal", "balanced")
    exclusions = preferences.get("exclude", [])
    requested_cuisine_raw = str(preferences.get("cuisine", "any"))
    if diet not in {"any", "vegetarian", "vegan"}:
        raise ValueError("invalid diet preference")
    if not isinstance(max_minutes, (int, float)) or not 1 <= max_minutes <= 90:
        raise ValueError("invalid time preference")
    if goal not in {"balanced", "protein", "light"} or not isinstance(exclusions, list) or len(exclusions) > 24 or not all(isinstance(item, str) and len(item) <= 80 for item in exclusions):
        raise ValueError("invalid meal preferences")

    catalog = Path(os.getenv("RECIPE_CATALOG_PATH", Path(__file__).resolve().parent.parent / "data/raw/food-com/RAW_recipes.csv"))
    if not catalog.is_file():
        raise RuntimeError(f"Recipe catalog is missing: {catalog}")

    def normalize(value: str) -> str:
        value = unicodedata.normalize("NFD", value.lower())
        value = "".join(char for char in value if unicodedata.category(char) != "Mn")
        return re.sub(r"[^a-z0-9]+", " ", value).strip()

    requested_cuisine = normalize(requested_cuisine_raw)
    if not isinstance(limit, int) or not 1 <= limit <= 12:
        raise ValueError("limit must be between 1 and 12")
    stop_words = {"the", "and", "with", "for", "something", "meal", "meals", "food", "please", "suggest", "recommend", "ideas", "some", "under", "within", "minutes", "minute"}
    tokens = [word for word in normalize(query).split() if len(word) > 2 and word not in stop_words]
    exclusion_terms = set()
    aliases = {"meat": {"beef", "chicken", "pork", "turkey", "lamb", "bacon", "ham", "sausage"}, "dairy": {"milk", "cheese", "butter", "cream", "yogurt"}, "seafood": {"fish", "salmon", "tuna", "shrimp", "prawn", "shellfish"}}
    for exclusion in exclusions:
        normalized = normalize(exclusion)
        exclusion_terms.update(aliases.get(normalized, {normalized}))

    candidates = []
    meal_courses = {"main dish", "lunch", "dinner"}
    if "breakfast" in tokens:
        meal_courses.add("breakfast")
    cuisine_tags = {"italian", "mexican", "chinese", "japanese", "korean", "thai", "indian", "greek", "french", "mediterranean", "middle eastern", "american", "canadian"}
    with catalog.open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            try:
                recipe_id = int(row["id"])
                minutes = int(row["minutes"])
                tags = [normalize(tag) for tag in ast.literal_eval(row["tags"])]
                ingredients = [str(item) for item in ast.literal_eval(row["ingredients"])]
                nutrition = ast.literal_eval(row["nutrition"])
            except (ValueError, TypeError, SyntaxError, KeyError):
                continue
            if minutes < 1 or minutes > max_minutes:
                continue
            if diet == "vegetarian" and not {"vegetarian", "vegan"}.intersection(tags):
                continue
            if diet == "vegan" and "vegan" not in tags:
                continue
            if not meal_courses.intersection(tags):
                continue
            ingredient_text = normalize(" ".join(ingredients))
            tag_text = " ".join(tags)
            if any(re.search(rf"\b{re.escape(term)}\b", ingredient_text) for term in exclusion_terms if term):
                continue
            if requested_cuisine != "any" and requested_cuisine not in tag_text:
                continue
            title = row["name"].strip()
            description = row.get("description", "").strip()
            searchable = normalize(" ".join([title, description, ingredient_text, tag_text]))
            matched = sum(token in searchable for token in tokens)
            content_score = matched / len(tokens) if tokens else 0.5
            if goal == "light" and nutrition:
                content_score = 0.7 * content_score + 0.3 * max(0.0, 1 - float(nutrition[0]) / 1500)
            elif goal == "protein" and len(nutrition) > 4:
                content_score = 0.7 * content_score + 0.3 * min(float(nutrition[4]) / 100, 1.0)
            content_score = max(0.0, min(1.0, content_score))
            if tokens and not matched:
                continue
            try:
                calories = round(float(nutrition[0]))
                protein = round(float(nutrition[4]) / 2)
                steps = [str(step) for step in ast.literal_eval(row["steps"])][:20]
            except (ValueError, TypeError, SyntaxError, IndexError, KeyError):
                calories, protein, steps = 0, 0, []
            recipe_tags = [tag.replace("-", " ") for tag in tags if tag not in {"time-to-make", "course", "main-ingredient", "preparation", "occasion", "dietary"}][:8]
            cuisine = next((tag.replace("-", " ").title() for tag in tags if tag in cuisine_tags), "Global")
            meal = {
                "id": f"foodcom:{recipe_id}",
                "title": title,
                "description": description[:1000],
                "cuisine": cuisine,
                "minutes": minutes,
                "calories": calories,
                "protein": protein,
                "tags": recipe_tags,
                "ingredients": ingredients[:40],
                "steps": steps,
                "image": "",
                "color": ["#dcefe7", "#f8e5c2", "#f4d8c5", "#f5e8bd"][recipe_id % 4],
            }
            entry = (content_score, -recipe_id, recipe_id, meal)
            if len(candidates) < 1000:
                heapq.heappush(candidates, entry)
            elif entry > candidates[0]:
                heapq.heapreplace(candidates, entry)

    if not candidates:
        return "[]"
    candidates.sort(key=lambda item: (-item[0], item[2]))
    scores = {recipe_id: score for score, _, recipe_id, _ in candidates}
    meals = {recipe_id: meal for _, _, recipe_id, meal in candidates}
    ranked = rank_foodcom_meals(list(meals), content_scores=[scores[recipe_id] for recipe_id in meals], limit=limit)
    recommendations = []
    for item in ranked["recommendations"]:
        meal = meals.get(item["recipe_id"])
        if meal is None:
            continue
        reasons = ["Returned by Remymy's Food.com MCP recommender.", f"{meal['minutes']} min, within your {max_minutes}-minute limit."]
        if diet != "any":
            reasons.append(f"Tagged {diet} by the source catalog.")
        recommendations.append({"meal": meal, "score": item["score"], "reasons": reasons})
    return json.dumps(recommendations)


@mcp.tool()
def rank_foodcom_meals(
    candidate_recipe_ids: list[int],
    user_id: int | None = None,
    content_scores: list[float] | None = None,
    limit: int = 6,
) -> dict:
    """Rank already-filtered Food.com candidates with the hybrid model.

    Dietary, allergen, time, and other hard constraints must be applied before
    this tool is called. ``content_scores`` are 0..1 scores from Remymy's
    content/query retriever in the same order as ``candidate_recipe_ids``.
    """

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
        "collaborative_weight": 0.75,
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
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError("Remymy's ranking service is unavailable") from exc


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )
