#!/usr/bin/env python3
"""Profile Food.com's tokenized recipes and chronological interaction splits."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from hybrid import SPECIAL_TOKENS, TokenMapper


def parse_json_list(value: str) -> list[Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("expected a JSON-compatible list")
    return parsed


def profile(data_dir: Path, vocab_path: Path | None = None) -> dict[str, Any]:
    split_names = ["train", "validation", "test"]
    splits = {
        name: pd.read_csv(data_dir / f"interactions_{name}.csv", parse_dates=["date"])
        for name in split_names
    }
    recipes = pd.read_csv(
        data_dir / "PP_recipes.csv",
        usecols=["id", "i", "name_tokens", "ingredient_tokens", "steps_tokens", "techniques", "calorie_level", "ingredient_ids"],
    )
    raw_recipes = pd.read_csv(data_dir / "RAW_recipes.csv", usecols=["id", "name"])
    recipe_names = raw_recipes.set_index("id")["name"].to_dict()

    train = splits["train"]
    train_users = set(train["u"].unique().tolist())
    train_items = set(train["i"].unique().tolist())
    interaction_rows = int(sum(len(frame) for frame in splits.values()))
    n_users = int(max(frame["u"].max() for frame in splits.values())) + 1
    n_items = int(max(frame["i"].max() for frame in splits.values())) + 1
    rating_counts = Counter()
    for frame in splits.values():
        rating_counts.update(frame["rating"].astype(float).tolist())

    split_profile: dict[str, Any] = {}
    for name, frame in splits.items():
        duplicate_pairs = int(frame.duplicated(["u", "i"]).sum())
        split_profile[name] = {
            "rows": int(len(frame)),
            "users": int(frame["u"].nunique()),
            "items": int(frame["i"].nunique()),
            "date_min": frame["date"].min().date().isoformat(),
            "date_max": frame["date"].max().date().isoformat(),
            "duplicate_user_item_rows": duplicate_pairs,
            "cold_users_vs_train": int((~frame["u"].isin(train_users)).sum()) if name != "train" else 0,
            "cold_items_vs_train": int((~frame["i"].isin(train_items)).sum()) if name != "train" else 0,
        }

    train_max_date = train.groupby("u")["date"].max()
    chronology: dict[str, Any] = {}
    for name in ["validation", "test"]:
        frame = splits[name]
        prior_max = frame["u"].map(train_max_date)
        chronology[name] = {
            "rows_before_user_train_history": int((frame["date"] < prior_max).sum()),
            "rows_without_user_train_history": int(prior_max.isna().sum()),
        }

    pair_sets = {
        name: set(zip(frame["u"].astype(int), frame["i"].astype(int)))
        for name, frame in splits.items()
    }
    overlaps = {
        "train_validation": len(pair_sets["train"] & pair_sets["validation"]),
        "train_test": len(pair_sets["train"] & pair_sets["test"]),
        "validation_test": len(pair_sets["validation"] & pair_sets["test"]),
    }
    item_overlaps = {
        "train_validation": len(set(splits["train"]["i"]) & set(splits["validation"]["i"])),
        "train_test": len(set(splits["train"]["i"]) & set(splits["test"]["i"])),
        "validation_test": len(set(splits["validation"]["i"]) & set(splits["test"]["i"])),
    }

    mapper = TokenMapper(vocab_path)
    token_columns = ["name_tokens", "ingredient_tokens", "steps_tokens"]
    token_lengths: dict[str, list[int]] = {column: [] for column in token_columns}
    special_counts = Counter()
    max_token_id = -1
    malformed = Counter()
    samples: list[dict[str, Any]] = []
    for row_number, row in enumerate(recipes.itertuples(index=False)):
        parsed: dict[str, list[Any]] = {}
        for column in token_columns:
            try:
                parsed[column] = parse_json_list(getattr(row, column))
            except (json.JSONDecodeError, TypeError, ValueError):
                malformed[column] += 1
                parsed[column] = []
        name_ids = [int(value) for value in parsed["name_tokens"]]
        ingredient_nested = parsed["ingredient_tokens"]
        ingredient_ids = [int(value) for group in ingredient_nested for value in group]
        step_ids = [int(value) for value in parsed["steps_tokens"]]
        token_lengths["name_tokens"].append(len(name_ids))
        token_lengths["ingredient_tokens"].append(len(ingredient_nested))
        token_lengths["steps_tokens"].append(len(step_ids))
        all_ids = name_ids + ingredient_ids + step_ids
        if all_ids:
            max_token_id = max(max_token_id, max(all_ids))
            special_counts.update(value for value in all_ids if value in SPECIAL_TOKENS)
        if row_number < 5:
            samples.append(
                {
                    "recipe_id": int(row.id),
                    "dense_item_index": int(row.i),
                    "raw_name": recipe_names.get(int(row.id)),
                    "name_token_ids": name_ids,
                    "name_tokens": mapper.tokens(name_ids),
                    "decoded_name": mapper.text(name_ids),
                }
            )

    length_summary = {}
    for column, values in token_lengths.items():
        array = np.asarray(values)
        length_summary[column] = {
            "min": int(array.min()),
            "median": float(np.median(array)),
            "p95": float(np.quantile(array, 0.95)),
            "max": int(array.max()),
        }

    user_counts = train.groupby("u").size().to_numpy()
    item_counts = train.groupby("i").size().to_numpy()
    return {
        "source": {
            "dataset": "shuyangli94/food-com-recipes-and-user-interactions",
            "archive_sha256": "e7a43e623c616799f78a0479b550483450fd7ede4e0c40979db3458a82b5b913",
            "version": 2,
        },
        "grain": "one explicit rating event per user-recipe pair in the provided chronological split files",
        "recipes": {
            "rows": int(len(recipes)),
            "unique_recipe_ids": int(recipes["id"].nunique()),
            "unique_dense_item_indices": int(recipes["i"].nunique()),
            "duplicate_recipe_ids": int(recipes.duplicated("id").sum()),
            "duplicate_dense_item_indices": int(recipes.duplicated("i").sum()),
            "calorie_levels": {str(key): int(value) for key, value in recipes["calorie_level"].value_counts().sort_index().items()},
        },
        "interactions": {
            "rows": interaction_rows,
            "users": n_users,
            "items": n_items,
            "matrix_sparsity": 1.0 - interaction_rows / float(n_users * n_items),
            "rating_distribution": {str(key): int(value) for key, value in sorted(rating_counts.items())},
            "train_interactions_per_user": {
                "median": float(np.median(user_counts)),
                "p95": float(np.quantile(user_counts, 0.95)),
                "max": int(user_counts.max()),
            },
            "train_interactions_per_item": {
                "median": float(np.median(item_counts)),
                "p95": float(np.quantile(item_counts, 0.95)),
                "max": int(item_counts.max()),
            },
            "splits": split_profile,
            "pair_overlap_across_splits": overlaps,
            "item_overlap_across_splits": item_overlaps,
            "provided_split_warning": "Validation and test items have zero overlap with training items; these are cold-recipe generation splits and cannot evaluate collaborative item factors.",
            "chronology": chronology,
        },
        "tokens": {
            "base_vocabulary_size": 40478,
            "total_vocabulary_size_with_specials": 40483,
            "special_token_map": {str(key): value for key, value in SPECIAL_TOKENS.items()},
            "special_token_counts": {str(key): int(value) for key, value in sorted(special_counts.items())},
            "max_observed_token_id": int(max_token_id),
            "malformed_rows": dict(malformed),
            "lengths": length_summary,
            "samples": samples,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--vocab", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = profile(args.data_dir, args.vocab)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
