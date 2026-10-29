"""Quadratic-weighted Cohen's kappa for ordinal human and judge grades."""

from __future__ import annotations


def quadratic_weighted_kappa(human: list[int], judge: list[int]) -> float | None:
    if len(human) != len(judge):
        raise ValueError("human and judge grade lists must have equal length")
    if not human:
        return None
    if any(type(grade) is not int or not 0 <= grade <= 3 for grade in (*human, *judge)):
        raise ValueError("grades must be integers from 0 to 3")

    size = 4
    observed = [[0] * size for _ in range(size)]
    for human_grade, judge_grade in zip(human, judge, strict=True):
        observed[human_grade][judge_grade] += 1
    human_counts = [sum(row) for row in observed]
    judge_counts = [sum(observed[row][column] for row in range(size)) for column in range(size)]

    def weight(left: int, right: int) -> float:
        return ((left - right) / (size - 1)) ** 2

    observed_disagreement = sum(
        observed[left][right] * weight(left, right)
        for left in range(size) for right in range(size)
    ) / len(human)
    expected_disagreement = sum(
        human_counts[left] * judge_counts[right] * weight(left, right)
        for left in range(size) for right in range(size)
    ) / (len(human) ** 2)
    if expected_disagreement == 0:
        return 1.0 if observed_disagreement == 0 else None
    return 1 - observed_disagreement / expected_disagreement
