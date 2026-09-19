"""Hybrid collaborative/content ranking primitives for Remymy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np


SPECIAL_TOKENS = {
    40478: "</s>",   # end of an instruction step
    40479: "</p>",   # padding
    40480: "<R>",    # start of recipe/document
    40481: "</R>",   # end of recipe/document
    40482: "<s>",    # start of an instruction step
}


@dataclass(frozen=True)
class Candidate:
    recipe_id: int
    content_score: float = 0.0
    eligible: bool = True


class TokenMapper:
    """Decode the legacy OpenAI-GPT IDs used by ``PP_recipes.csv``."""

    def __init__(self, vocab_path: str | Path | None = None) -> None:
        self.decoder: dict[int, str] = dict(SPECIAL_TOKENS)
        if vocab_path:
            with Path(vocab_path).open(encoding="utf-8") as handle:
                encoder = json.load(handle)
            self.decoder.update({int(index): token for token, index in encoder.items()})

    def tokens(self, ids: Iterable[int]) -> list[str]:
        return [self.decoder.get(int(token_id), f"<unknown:{token_id}>") for token_id in ids]

    def text(self, ids: Iterable[int]) -> str:
        raw = "".join(self.tokens(ids))
        return (
            raw.replace("<R>", "")
            .replace("</R>", "")
            .replace("</p>", "")
            .replace("<s>", "")
            .replace("</s>", ". ")
            .replace("</w>", " ")
            .strip()
        )


class HybridRanker:
    """Serve latent-factor scores with content/popularity cold-start fallbacks."""

    def __init__(self, artifact_path: str | Path) -> None:
        artifact = np.load(artifact_path, allow_pickle=False)
        self.user_ids = artifact["user_ids"].astype(np.int64, copy=False)
        self.item_ids = artifact["item_ids"].astype(np.int64, copy=False)
        self.user_factors = artifact["user_factors"].astype(np.float32, copy=False)
        self.item_factors = artifact["item_factors"].astype(np.float32, copy=False)
        self.user_bias = artifact["user_bias"].astype(np.float32, copy=False)
        self.item_bias = artifact["item_bias"].astype(np.float32, copy=False)
        self.popularity = artifact["popularity"].astype(np.float32, copy=False)
        self.global_mean = float(artifact["global_mean"])
        self.score_mode = int(artifact["score_mode"]) if "score_mode" in artifact.files else 0
        self.seen_indptr = artifact["seen_indptr"].astype(np.int64, copy=False)
        self.seen_indices = artifact["seen_indices"].astype(np.int32, copy=False)
        self.user_index = {int(value): index for index, value in enumerate(self.user_ids)}
        self.item_index = {int(value): index for index, value in enumerate(self.item_ids)}

    def _seen(self, user_index: int, item_index: int) -> bool:
        start, end = self.seen_indptr[user_index : user_index + 2]
        history = self.seen_indices[start:end]
        position = np.searchsorted(history, item_index)
        return bool(position < history.size and history[position] == item_index)

    def _cf_score(self, user_index: int, item_index: int) -> float:
        raw_score = (
            self.global_mean
            + float(self.user_bias[user_index])
            + float(self.item_bias[item_index])
            + float(self.user_factors[user_index] @ self.item_factors[item_index])
        )
        if self.score_mode == 1:
            return float(1.0 / (1.0 + np.exp(-np.clip(raw_score, -20.0, 20.0))))
        return float(np.clip((raw_score - 1.0) / 4.0, 0.0, 1.0))

    def rank(
        self,
        user_id: int | None,
        candidates: Iterable[Candidate],
        *,
        limit: int = 6,
        collaborative_weight: float = 0.75,
        filter_seen: bool = True,
    ) -> list[dict[str, object]]:
        bounded_limit = max(0, min(int(limit), 100))
        collaborative_weight = float(np.clip(collaborative_weight, 0.0, 1.0))
        user_index = self.user_index.get(int(user_id)) if user_id is not None else None
        ranked: list[dict[str, object]] = []

        for candidate in candidates:
            if not candidate.eligible:
                continue
            item_index = self.item_index.get(int(candidate.recipe_id))
            item_is_warm = item_index is not None and self.popularity[item_index] > 0
            if item_index is None:
                collaborative_score = 0.0
                popularity_score = 0.0
            else:
                popularity_score = float(self.popularity[item_index])
                if user_index is not None and filter_seen and self._seen(user_index, item_index):
                    continue
                collaborative_score = (
                    self._cf_score(user_index, item_index)
                    if user_index is not None and item_is_warm
                    else popularity_score
                )

            content_score = float(np.clip(candidate.content_score, 0.0, 1.0))
            if user_index is not None and item_is_warm:
                final_score = (
                    collaborative_weight * collaborative_score
                    + (1.0 - collaborative_weight) * content_score
                )
                source = "hybrid"
                reasons = ["matches similar users' historical ratings", "matches the current request"]
            else:
                final_score = 0.75 * content_score + 0.25 * popularity_score
                source = "cold_start"
                reasons = ["content/popularity fallback; no learned user-item vector"]

            ranked.append(
                {
                    "recipe_id": int(candidate.recipe_id),
                    "score": round(float(final_score), 6),
                    "collaborative_score": round(collaborative_score, 6),
                    "content_score": round(content_score, 6),
                    "source": source,
                    "reason_codes": reasons,
                }
            )

        ranked.sort(key=lambda row: (-float(row["score"]), int(row["recipe_id"])))
        return ranked[:bounded_limit]
