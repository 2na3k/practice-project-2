#!/usr/bin/env python3
"""Train a compact biased matrix-factorization model on Food.com interactions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def load_split(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, usecols=["user_id", "recipe_id", "rating", "u", "i"])
    return frame.astype({"user_id": "int64", "recipe_id": "int64", "rating": "float32", "u": "int32", "i": "int32"})


def build_id_map(frame: pd.DataFrame, index_column: str, id_column: str, size: int) -> np.ndarray:
    mapping = np.full(size, -1, dtype=np.int64)
    pairs = frame[[index_column, id_column]].drop_duplicates(index_column)
    mapping[pairs[index_column].to_numpy(dtype=np.int64)] = pairs[id_column].to_numpy(dtype=np.int64)
    if np.any(mapping < 0):
        raise ValueError(f"{id_column} mapping does not cover every dense {index_column} index")
    return mapping


def build_seen(u: np.ndarray, i: np.ndarray, n_users: int) -> tuple[np.ndarray, np.ndarray]:
    pairs = np.unique(np.column_stack((u, i)), axis=0)
    order = np.lexsort((pairs[:, 1], pairs[:, 0]))
    pairs = pairs[order]
    counts = np.bincount(pairs[:, 0], minlength=n_users)
    indptr = np.empty(n_users + 1, dtype=np.int64)
    indptr[0] = 0
    np.cumsum(counts, out=indptr[1:])
    return indptr, pairs[:, 1].astype(np.int32, copy=False)


def predict(
    u: np.ndarray,
    i: np.ndarray,
    global_mean: float,
    user_bias: np.ndarray,
    item_bias: np.ndarray,
    user_factors: np.ndarray,
    item_factors: np.ndarray,
) -> np.ndarray:
    return global_mean + user_bias[u] + item_bias[i] + np.sum(user_factors[u] * item_factors[i], axis=1)


def regression_metrics(frame: pd.DataFrame, arrays: tuple[float, np.ndarray, np.ndarray, np.ndarray, np.ndarray]) -> dict[str, float]:
    global_mean, user_bias, item_bias, user_factors, item_factors = arrays
    actual = frame["rating"].to_numpy(dtype=np.float32)
    estimated = np.clip(
        predict(
            frame["u"].to_numpy(dtype=np.int32),
            frame["i"].to_numpy(dtype=np.int32),
            global_mean,
            user_bias,
            item_bias,
            user_factors,
            item_factors,
        ),
        1.0,
        5.0,
    )
    error = actual - estimated
    return {"rmse": float(np.sqrt(np.mean(error**2))), "mae": float(np.mean(np.abs(error)))}


def sampled_ranking_metrics(
    validation: pd.DataFrame,
    *,
    arrays: tuple[float, np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    popularity: np.ndarray,
    seen_indptr: np.ndarray,
    seen_indices: np.ndarray,
    samples: int,
    seed: int,
) -> dict[str, dict[str, float]]:
    positives = validation[validation["rating"] >= 4.0]
    if len(positives) > samples:
        positives = positives.sample(samples, random_state=seed)
    rng = np.random.default_rng(seed)
    warm_items = np.flatnonzero(popularity > 0).astype(np.int32)
    output = {"matrix_factorization": {"hit_rate_at_10": 0.0, "mrr_at_10": 0.0, "ndcg_at_10": 0.0}, "popularity": {"hit_rate_at_10": 0.0, "mrr_at_10": 0.0, "ndcg_at_10": 0.0}}
    global_mean, user_bias, item_bias, user_factors, item_factors = arrays

    for row in positives.itertuples(index=False):
        start, end = seen_indptr[row.u : row.u + 2]
        blocked = set(seen_indices[start:end].tolist())
        blocked.add(int(row.i))
        negatives: list[int] = []
        while len(negatives) < 99:
            for candidate in rng.choice(warm_items, size=min(160, warm_items.size), replace=False):
                value = int(candidate)
                if value not in blocked:
                    blocked.add(value)
                    negatives.append(value)
                    if len(negatives) == 99:
                        break
        items = np.asarray([int(row.i), *negatives], dtype=np.int32)
        users = np.full(items.size, int(row.u), dtype=np.int32)
        model_scores = predict(users, items, global_mean, user_bias, item_bias, user_factors, item_factors)
        for key, scores in (("matrix_factorization", model_scores), ("popularity", popularity[items])):
            rank = int(np.sum(scores[1:] > scores[0]) + np.sum((scores[1:] == scores[0]) & (items[1:] < items[0])) + 1)
            if rank <= 10:
                output[key]["hit_rate_at_10"] += 1.0
                output[key]["mrr_at_10"] += 1.0 / rank
                output[key]["ndcg_at_10"] += 1.0 / np.log2(rank + 1)

    denominator = max(len(positives), 1)
    for values in output.values():
        for metric in values:
            values[metric] /= denominator
        values["evaluated_positives"] = int(len(positives))
        values["negative_samples_per_positive"] = 99
    return output


def train(args: argparse.Namespace) -> dict[str, object]:
    train_frame = load_split(args.data_dir / "interactions_train.csv")
    validation = load_split(args.data_dir / "interactions_validation.csv")
    test = load_split(args.data_dir / "interactions_test.csv")
    all_rows = pd.concat([train_frame, validation, test], ignore_index=True)
    n_users = int(all_rows["u"].max()) + 1
    n_items = int(all_rows["i"].max()) + 1
    user_ids = build_id_map(all_rows, "u", "user_id", n_users)
    item_ids = build_id_map(all_rows, "i", "recipe_id", n_items)

    u = train_frame["u"].to_numpy(dtype=np.int32)
    i = train_frame["i"].to_numpy(dtype=np.int32)
    ratings = train_frame["rating"].to_numpy(dtype=np.float32)
    global_mean = float(ratings.mean()) if args.objective == "explicit" else 0.0
    rng = np.random.default_rng(args.seed)
    scale = 0.05
    user_factors = rng.normal(0.0, scale, size=(n_users, args.factors)).astype(np.float32)
    item_factors = rng.normal(0.0, scale, size=(n_items, args.factors)).astype(np.float32)
    user_bias = np.zeros(n_users, dtype=np.float32)
    item_bias = np.zeros(n_items, dtype=np.float32)
    epoch_metrics: list[dict[str, float]] = []

    warm_items = np.unique(i)
    positive_rows = np.flatnonzero(ratings >= 4.0)
    for epoch in range(args.epochs):
        learning_rate = args.learning_rate * (args.decay**epoch)
        if args.objective == "bpr":
            order = rng.permutation(positive_rows)
            epoch_loss = 0.0
            observations = 0
            for offset in range(0, len(order), args.batch_size):
                batch = order[offset : offset + args.batch_size]
                batch_u, positive_i = u[batch], i[batch]
                negative_i = rng.choice(warm_items, size=len(batch), replace=True).astype(np.int32)
                collision = negative_i == positive_i
                while collision.any():
                    negative_i[collision] = rng.choice(warm_items, size=int(collision.sum()), replace=True)
                    collision = negative_i == positive_i
                current_u = user_factors[batch_u].copy()
                current_positive = item_factors[positive_i].copy()
                current_negative = item_factors[negative_i].copy()
                margin = item_bias[positive_i] - item_bias[negative_i] + np.sum(current_u * (current_positive - current_negative), axis=1)
                gradient = 1.0 / (1.0 + np.exp(np.clip(margin, -20.0, 20.0)))
                np.add.at(user_factors, batch_u, learning_rate * (gradient[:, None] * (current_positive - current_negative) - args.regularization * current_u))
                np.add.at(item_factors, positive_i, learning_rate * (gradient[:, None] * current_u - args.regularization * current_positive))
                np.add.at(item_factors, negative_i, learning_rate * (-gradient[:, None] * current_u - args.regularization * current_negative))
                np.add.at(item_bias, positive_i, learning_rate * (gradient - args.regularization * item_bias[positive_i]))
                np.add.at(item_bias, negative_i, learning_rate * (-gradient - args.regularization * item_bias[negative_i]))
                epoch_loss += float(np.logaddexp(0.0, -margin).sum())
                observations += len(batch)
            metrics = {"epoch": epoch + 1, "pairwise_log_loss": epoch_loss / max(observations, 1)}
        else:
            order = rng.permutation(len(ratings))
            for offset in range(0, len(order), args.batch_size):
                batch = order[offset : offset + args.batch_size]
                batch_u, batch_i, batch_r = u[batch], i[batch], ratings[batch]
                current_u = user_factors[batch_u].copy()
                current_i = item_factors[batch_i].copy()
                estimate = global_mean + user_bias[batch_u] + item_bias[batch_i] + np.sum(current_u * current_i, axis=1)
                error = np.clip(batch_r - estimate, -3.0, 3.0)
                np.add.at(user_bias, batch_u, learning_rate * (error - args.regularization * user_bias[batch_u]))
                np.add.at(item_bias, batch_i, learning_rate * (error - args.regularization * item_bias[batch_i]))
                np.add.at(user_factors, batch_u, learning_rate * (error[:, None] * current_i - args.regularization * current_u))
                np.add.at(item_factors, batch_i, learning_rate * (error[:, None] * current_u - args.regularization * current_i))
            metrics = regression_metrics(validation, (global_mean, user_bias, item_bias, user_factors, item_factors))
            metrics["epoch"] = epoch + 1
        epoch_metrics.append(metrics)
        print(json.dumps(metrics, sort_keys=True))

    counts = np.bincount(i, minlength=n_items).astype(np.float32)
    popularity = np.log1p(counts)
    if popularity.max() > 0:
        popularity /= popularity.max()
    seen_indptr, seen_indices = build_seen(u, i, n_users)
    arrays = (global_mean, user_bias, item_bias, user_factors, item_factors)
    warm_item_indices = set(np.flatnonzero(counts > 0).tolist())
    warm_validation = validation[validation["i"].isin(warm_item_indices)]
    warm_test = test[test["i"].isin(warm_item_indices)]
    evaluation = {
        "validation": {"rows": int(len(warm_validation)), "coverage": float(len(warm_validation) / max(len(validation), 1))},
        "test": {"rows": int(len(warm_test)), "coverage": float(len(warm_test) / max(len(test), 1))},
        "sampled_ranking": sampled_ranking_metrics(
            warm_validation,
            arrays=arrays,
            popularity=popularity,
            seen_indptr=seen_indptr,
            seen_indices=seen_indices,
            samples=args.ranking_samples,
            seed=args.seed,
        ),
    }
    if args.objective == "explicit":
        evaluation["validation"].update(regression_metrics(warm_validation, arrays))
        evaluation["test"].update(regression_metrics(warm_test, arrays))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.output,
        user_ids=user_ids,
        item_ids=item_ids,
        user_factors=user_factors,
        item_factors=item_factors,
        user_bias=user_bias,
        item_bias=item_bias,
        popularity=popularity,
        global_mean=np.asarray(global_mean, dtype=np.float32),
        score_mode=np.asarray(1 if args.objective == "bpr" else 0, dtype=np.int8),
        seen_indptr=seen_indptr,
        seen_indices=seen_indices,
    )
    manifest = {
        "model": "bpr_matrix_factorization_hybrid_v1" if args.objective == "bpr" else "biased_matrix_factorization_hybrid_v1",
        "objective": args.objective,
        "artifact": str(args.output),
        "training_rows": int(len(train_frame)),
        "users": n_users,
        "items": n_items,
        "factors": args.factors,
        "epochs": args.epochs,
        "seed": args.seed,
        "rating_global_mean": global_mean,
        "epoch_metrics": epoch_metrics,
        "evaluation": evaluation,
        "evaluation_note": "Ranking metrics use 99 deterministically sampled unseen negatives per held-out positive; they are directional, not full-catalog metrics.",
    }
    manifest_path = args.output.with_suffix(".json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--factors", type=int, default=24)
    parser.add_argument("--epochs", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--learning-rate", type=float, default=0.002)
    parser.add_argument("--decay", type=float, default=0.9)
    parser.add_argument("--regularization", type=float, default=0.03)
    parser.add_argument("--ranking-samples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--objective", choices=["bpr", "explicit"], default="bpr")
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(train(parse_args()), indent=2, sort_keys=True))
