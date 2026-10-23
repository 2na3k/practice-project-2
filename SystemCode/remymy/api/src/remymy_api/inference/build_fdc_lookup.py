"""Build api/src/remymy_api/inference/weights/fdc_lookup.json: every training food keyed by fdc_id, as the recipe models saw it.

Sources are the files the training notebooks wrote, so a lookup reproduces the training inputs exactly:
- recipe_clusters.csv (recipe_clustering.ipynb): description, product / adj / verb, nutrients, price interval, cluster;
- recipe_classes.csv (recipe_labeling.ipynb, input of recipe_classification.ipynb): the class labels.
Rerun after retraining either model.
"""
import argparse
import ast
import json
from pathlib import Path

import pandas as pd

try:
    from .food_query import DEFAULT_LOOKUP, NUTRIENT_COLS
except ImportError:  # run as a file
    from food_query import DEFAULT_LOOKUP, NUTRIENT_COLS

DROPPED_LABELS = {"fusion_cuisine"}  # too rare to learn, dropped by recipe_classification.ipynb


def _list(value):
    return ast.literal_eval(value) if isinstance(value, str) else []


def _number(value):
    return None if pd.isna(value) else float(value)


def build(clusters_csv, classes_csv):
    clusters = pd.read_csv(clusters_csv)
    classes = pd.read_csv(classes_csv).set_index("fdc_id")
    lookup = {}
    for row in clusters.itertuples(index=False):
        # the classifier's targets: fusion_cuisine dropped, a row left without labels is generic
        labels = [x for x in str(classes.at[row.fdc_id, "labels"]).split("|")
                  if x and x != "nan" and x not in DROPPED_LABELS] or ["generic"]
        lookup[int(row.fdc_id)] = {
            "description": row.description,
            "product": _list(row.product), "adj": _list(row.adj), "verb": _list(row.verb),
            "nutrients": {c: _number(getattr(row, c)) for c in NUTRIENT_COLS},
            "price": float(row.price_mid),
            "price_interval": [float(row.price_lo), float(row.price_hi)],  # ±3 per unpriced item
            "unpriced_items": _list(row.unpriced_items),
            "training": {"cluster": int(row.cluster), "labels": labels},
        }
    return lookup


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--clusters", type=Path, required=True, help="recipe_clusters.csv of recipe_clustering.ipynb")
    parser.add_argument("--classes", type=Path, required=True, help="recipe_classes.csv used by recipe_classification.ipynb")
    parser.add_argument("--out", type=Path, default=DEFAULT_LOOKUP)
    args = parser.parse_args()

    lookup = build(args.clusters, args.classes)
    args.out.write_text(json.dumps(lookup, separators=(",", ":")))
    print(f"wrote {len(lookup)} foods to {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
