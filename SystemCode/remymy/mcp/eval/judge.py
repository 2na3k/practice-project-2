"""Jev candidate grader used by the offline evaluation runner."""

from __future__ import annotations

import math

from core.jev import JevClient, JevError, score

JUDGE_VERSION = "jev-fit-grade-v1"
JUDGE_LEVELS = [
    "Unsuitable: breaks a restriction or does not fit the request",
    "Marginal: fits some requirements and misses others",
    "Relevant: fits most requirements within reasonable tolerance",
    "Highly relevant: closely fits the request and respects restrictions",
]
JUDGE_QUESTION = score(
    instructions="Grade how well this recipe fits the request based on its ingredients, steps, and nutrition.",
    levels=JUDGE_LEVELS,
)


def judge_one(client: JevClient, request_payload: dict, recipe_payload: dict) -> dict:
    recipe = {key: recipe_payload.get(key) for key in
              ("recipe_id", "title", "ingredients", "steps", "nutrition")}
    answers = client.ask({"request": request_payload, "recipe": recipe}, {"fit": JUDGE_QUESTION})
    answer = answers.get("fit", {})
    value = answer.get("score")
    if not isinstance(value, (int, float)) or not 0 <= value <= 3:
        raise JevError("Jev judge returned no usable fit score")
    return {"score": float(value), "probabilities": answer.get("probabilities", {}),
            "model": client.model}


def judge_grades(judgments: dict[str, dict]) -> dict[str, int]:
    """Convert judge scores to integer grades with half-up rounding."""
    return {recipe_id: min(3, max(0, math.floor(result["score"] + 0.5)))
            for recipe_id, result in judgments.items()}
