"""Ranking metrics for a completely graded candidate pool."""

from itertools import combinations
from math import log2

from core.config import eval_k


def ranking_metrics(
        ranked_ids: list[str], grades: dict[str, int], k: int | None = None
) -> dict[str, float | bool | None]:
    if not ranked_ids or len(ranked_ids) != len(set(ranked_ids)):
        raise ValueError("ranking must be nonempty with unique recipe IDs")
    if set(ranked_ids) != set(grades):
        raise ValueError("grade every recipe in the recorded candidate pool, and no others")
    if any(type(grade) is not int or not 0 <= grade <= 3 for grade in grades.values()):
        raise ValueError("grades must be integers from 0 to 3")

    k = eval_k() if k is None else k
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    ordered = [grades[recipe_id] for recipe_id in ranked_ids]
    cutoff = min(k, len(ordered))
    relevant_count = sum(grade >= 2 for grade in ordered)

    def dcg(values: list[int]) -> float:
        return sum(
            (2 ** grade - 1) / log2(position + 2) for position, grade in enumerate(values[:k]))

    ideal = dcg(sorted(ordered, reverse=True))
    actual = dcg(ordered)
    gradable_pairs = [(left, right) for left, right in combinations(ordered, 2) if left != right]
    concordant_pairs = sum(left > right for left, right in gradable_pairs)

    return {
        f"nDCG@{k}": actual / ideal if ideal and relevant_count else None,
        f"Precision@{k}": sum(grade >= 2 for grade in ordered[:k]) / cutoff,
        f"Recall@{k}": sum(
            grade >= 2 for grade in ordered[:k]) / relevant_count if relevant_count else None,
        "pairwise_accuracy": concordant_pairs / len(gradable_pairs) if gradable_pairs else None,
        "pool_lt_k": len(ordered) < k,
    }
