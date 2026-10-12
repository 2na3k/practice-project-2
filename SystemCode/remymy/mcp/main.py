import json
import logging
import os
import time
import urllib.error
import urllib.request

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

# One line per record with time, level and module, as in the Data API, at the level LOG_LEVEL names.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
logging.basicConfig(
    level=LOG_LEVEL if isinstance(logging.getLevelName(LOG_LEVEL), int) else "INFO",
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("remymy.mcp")

mcp = MCPServer("Remymy MCP")


@mcp.tool()
def say_hello(name: str = "there") -> str:
    """Say hello to someone."""
    clean_name = name.strip() or "there"
    logger.debug("say_hello for %r.", clean_name)
    return f"Hello, {clean_name}! Welcome to Remymy."


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
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            ranked = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        logger.warning("Ranking %d candidates for user %s at %s failed: %s", len(candidate_recipe_ids), user_id,
                       endpoint, exc)
        raise RuntimeError("Remymy's ranking service is unavailable") from exc
    logger.info("Ranked %d candidates for user %s in %.2fs.", len(candidate_recipe_ids), user_id,
                time.perf_counter() - started)
    return ranked


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    logger.info("Starting the MCP service on port %s; ranking through %s.", os.getenv("PORT", "8000"),
                os.getenv("MODEL_URL", "http://localhost:8001"))
    mcp.run(
        transport="streamable-http",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )
