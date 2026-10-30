"""Baseline candidate orderings used for ranking comparisons."""

from math import log2


def _ordered(pool: list[dict], key: str) -> list[str]:
    return [str(item.get("recipe_id", item.get("id"))) for item in
            sorted(pool, key=lambda item: (-float(item.get(key) or 0), str(item.get("recipe_id", item.get("id")))))]


def content_order(pool: list[dict]) -> list[str]:
    return _ordered(pool, "content_score")


def popularity_order(pool: list[dict]) -> list[str]:
    return _ordered(pool, "popularity")


def popularity_novelty(popularity_scores: list[float]) -> float | None:
    """Mean information content; clamp zero scores to avoid log2(0)."""
    if not popularity_scores:
        return None
    return sum(-log2(max(float(value), 1e-12)) for value in popularity_scores) / len(popularity_scores)
