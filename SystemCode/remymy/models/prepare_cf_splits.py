#!/usr/bin/env python3
"""Create leakage-resistant chronological CF splits from RAW_interactions.csv."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def prepare(data_dir: Path, output_dir: Path) -> dict[str, object]:
    supplied = pd.concat(
        [
            pd.read_csv(data_dir / f"interactions_{name}.csv", usecols=["user_id", "u"])
            for name in ("train", "validation", "test")
        ],
        ignore_index=True,
    ).drop_duplicates("user_id")
    recipe_map = pd.read_csv(data_dir / "PP_recipes.csv", usecols=["id", "i"]).rename(columns={"id": "recipe_id"})
    raw = pd.read_csv(
        data_dir / "RAW_interactions.csv",
        usecols=["user_id", "recipe_id", "date", "rating"],
        parse_dates=["date"],
    )
    raw = raw.merge(supplied, on="user_id", how="inner", validate="many_to_one")
    raw = raw.merge(recipe_map, on="recipe_id", how="inner", validate="many_to_one")
    raw = raw.sort_values(["user_id", "recipe_id", "date"]).drop_duplicates(["user_id", "recipe_id"], keep="last")
    counts = raw.groupby("u").size()
    raw = raw[raw["u"].isin(counts[counts >= 3].index)].sort_values(["u", "date", "i"])
    raw["position_from_end"] = raw.groupby("u").cumcount(ascending=False)
    test = raw[raw["position_from_end"] == 0].copy()
    validation = raw[raw["position_from_end"] == 1].copy()
    train = raw[raw["position_from_end"] >= 2].copy()
    columns = ["user_id", "recipe_id", "date", "rating", "u", "i"]
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in (("train", train), ("validation", validation), ("test", test)):
        frame[columns].to_csv(output_dir / f"interactions_{name}.csv", index=False, date_format="%Y-%m-%d")

    train_items = set(train["i"].tolist())
    profile = {
        "source_rows": int(len(raw)),
        "deduplicated_user_recipe_grain": True,
        "users": int(raw["u"].nunique()),
        "items": int(raw["i"].nunique()),
        "train_rows": int(len(train)),
        "validation_rows": int(len(validation)),
        "test_rows": int(len(test)),
        "validation_warm_item_rate": float(validation["i"].isin(train_items).mean()),
        "test_warm_item_rate": float(test["i"].isin(train_items).mean()),
        "split_rule": "latest interaction per eligible user=test, second latest=validation, all earlier=train",
        "minimum_interactions_per_user": 3,
    }
    (output_dir / "manifest.json").write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.data_dir, args.output_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
