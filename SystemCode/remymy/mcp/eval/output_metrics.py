"""Metrics for the selected recommendation output."""

from __future__ import annotations

from itertools import combinations


def output_metrics(
    selected_ids: list[str], grades: dict[str, int],
    ingredients_by_id: dict[str, list[str]], pool_ids: list[str],
) -> dict[str, float | None]:
    selected_grades = [grades[str(recipe_id)] for recipe_id in selected_ids if str(recipe_id) in grades]
    plan_mean_grade = sum(selected_grades) / len(selected_grades) if selected_grades else None
    plan_share_good = sum(grade >= 2 for grade in selected_grades) / len(selected_grades) if selected_grades else None
    plan_repeat_rate = 1 - len(set(selected_ids)) / len(selected_ids) if selected_ids else None

    ingredient_sets = [set(ingredients_by_id.get(str(recipe_id), []))
                       for recipe_id in dict.fromkeys(map(str, selected_ids))]
    similarities = []
    for left, right in combinations(ingredient_sets, 2):
        union = left | right
        similarities.append(len(left & right) / len(union) if union else 1.0)
    diversity = 1 - sum(similarities) / len(similarities) if similarities else None
    pool_set = set(map(str, pool_ids))
    coverage = len(set(map(str, selected_ids)) & pool_set) / len(pool_set) if pool_set else None

    return {
        "plan_mean_grade": plan_mean_grade,
        "plan_share_good": plan_share_good,
        "plan_repeat_rate": plan_repeat_rate,
        "diversity": diversity,
        "coverage": coverage,
    }
