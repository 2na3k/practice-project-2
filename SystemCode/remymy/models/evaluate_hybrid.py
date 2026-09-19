#!/usr/bin/env python3
"""Evaluate BPR + ingredient-profile content scores on sampled candidates."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd


def history_bucket(size: int) -> str:
    if size <= 5:
        return "light_1_to_5"
    if size <= 20:
        return "medium_6_to_20"
    return "heavy_21_plus"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=6000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    train = pd.read_csv(args.data_dir / "interactions_train.csv", usecols=["u", "i", "rating"])
    validation = pd.read_csv(args.data_dir / "interactions_validation.csv", usecols=["u", "i", "rating"])
    artifact = np.load(args.artifact, allow_pickle=False)
    popularity = artifact["popularity"].astype(np.float32)
    warm_items = np.flatnonzero(popularity > 0).astype(np.int32)
    validation = validation[(validation["rating"] >= 4) & validation["i"].isin(warm_items)]
    if len(validation) > args.samples:
        validation = validation.sample(args.samples, random_state=args.seed)

    recipes = pd.read_csv(args.raw_dir / "PP_recipes.csv", usecols=["i", "ingredient_ids"])
    item_ingredients: list[np.ndarray] = [np.empty(0, dtype=np.int32) for _ in range(len(popularity))]
    document_frequency: Counter[int] = Counter()
    for row in recipes.itertuples(index=False):
        values = np.asarray(json.loads(row.ingredient_ids), dtype=np.int32)
        item_ingredients[int(row.i)] = values
        document_frequency.update(set(values.tolist()))
    max_ingredient = max(document_frequency) + 1
    idf = np.ones(max_ingredient, dtype=np.float32)
    for ingredient, frequency in document_frequency.items():
        idf[ingredient] = np.log((len(recipes) + 1) / (frequency + 1)) + 1.0

    sampled_users = set(validation["u"].astype(int).tolist())
    positive_history = train[(train["u"].isin(sampled_users)) & (train["rating"] >= 4)]
    user_profiles: dict[int, tuple[dict[int, float], float]] = {}
    for user, group in positive_history.groupby("u"):
        counts: Counter[int] = Counter()
        for item in group["i"]:
            counts.update(item_ingredients[int(item)].tolist())
        weighted = {ingredient: count * float(idf[ingredient]) for ingredient, count in counts.items()}
        norm = float(np.sqrt(sum(value * value for value in weighted.values())))
        user_profiles[int(user)] = (weighted, norm)

    def content_scores(user: int, items: np.ndarray) -> np.ndarray:
        weighted, user_norm = user_profiles.get(user, ({}, 0.0))
        output = np.zeros(len(items), dtype=np.float32)
        if user_norm == 0:
            return output
        for position, item in enumerate(items):
            ingredients = item_ingredients[int(item)]
            if ingredients.size == 0:
                continue
            item_weights = idf[ingredients]
            item_norm = float(np.linalg.norm(item_weights))
            dot = sum(weighted.get(int(ingredient), 0.0) * float(idf[int(ingredient)]) for ingredient in ingredients)
            output[position] = dot / max(user_norm * item_norm, 1e-8)
        return output

    rng = np.random.default_rng(args.seed)
    indptr, indices = artifact["seen_indptr"], artifact["seen_indices"]
    user_factors, item_factors = artifact["user_factors"], artifact["item_factors"]
    user_bias, item_bias = artifact["user_bias"], artifact["item_bias"]
    history_sizes = train.groupby("u").size().to_dict()
    methods = {
        "popularity": None,
        "content": None,
        "bpr": None,
        "hybrid_bpr_25_content_75": 0.25,
        "hybrid_bpr_50_content_50": 0.50,
        "hybrid_bpr_75_content_25": 0.75,
    }
    results = defaultdict(lambda: defaultdict(lambda: {"queries": 0, "hit_rate_at_10": 0.0, "mrr_at_10": 0.0, "ndcg_at_10": 0.0}))

    for row in validation.itertuples(index=False):
        start, end = indptr[row.u : row.u + 2]
        blocked = set(indices[start:end].tolist())
        blocked.add(int(row.i))
        negatives: list[int] = []
        while len(negatives) < 99:
            for candidate in rng.choice(warm_items, size=160, replace=False):
                value = int(candidate)
                if value not in blocked:
                    blocked.add(value)
                    negatives.append(value)
                    if len(negatives) == 99:
                        break
        items = np.asarray([int(row.i), *negatives], dtype=np.int32)
        raw_bpr = user_bias[row.u] + item_bias[items] + item_factors[items] @ user_factors[row.u]
        bpr = 1.0 / (1.0 + np.exp(-np.clip(raw_bpr, -20.0, 20.0)))
        content = content_scores(int(row.u), items)
        scores_by_method = {
            "popularity": popularity[items],
            "content": content,
            "bpr": bpr,
            "hybrid_bpr_25_content_75": 0.25 * bpr + 0.75 * content,
            "hybrid_bpr_50_content_50": 0.50 * bpr + 0.50 * content,
            "hybrid_bpr_75_content_25": 0.75 * bpr + 0.25 * content,
        }
        segment = history_bucket(int(history_sizes[row.u]))
        for method in methods:
            scores = scores_by_method[method]
            rank = int(np.sum(scores[1:] > scores[0]) + np.sum((scores[1:] == scores[0]) & (items[1:] < items[0])) + 1)
            for group in ("overall", segment):
                values = results[group][method]
                values["queries"] += 1
                if rank <= 10:
                    values["hit_rate_at_10"] += 1
                    values["mrr_at_10"] += 1 / rank
                    values["ndcg_at_10"] += 1 / np.log2(rank + 1)

    for method_values in results.values():
        for values in method_values.values():
            queries = max(values["queries"], 1)
            for metric in ("hit_rate_at_10", "mrr_at_10", "ndcg_at_10"):
                values[metric] /= queries
    payload = json.dumps(results, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
