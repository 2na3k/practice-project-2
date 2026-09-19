from __future__ import annotations

import numpy as np

from hybrid import Candidate, HybridRanker, TokenMapper


def artifact(path):
    np.savez_compressed(
        path,
        user_ids=np.asarray([10], dtype=np.int64),
        item_ids=np.asarray([100, 200, 300], dtype=np.int64),
        user_factors=np.asarray([[1.0, 0.0]], dtype=np.float32),
        item_factors=np.asarray([[0.0, 0.0], [1.0, 0.0], [0.25, 0.0]], dtype=np.float32),
        user_bias=np.asarray([0.0], dtype=np.float32),
        item_bias=np.asarray([0.0, 0.0, 0.0], dtype=np.float32),
        popularity=np.asarray([0.9, 0.2, 0.4], dtype=np.float32),
        global_mean=np.asarray(4.0, dtype=np.float32),
        seen_indptr=np.asarray([0, 1], dtype=np.int64),
        seen_indices=np.asarray([0], dtype=np.int32),
    )


def test_special_tokens_are_explained():
    mapper = TokenMapper()
    assert mapper.tokens([40480, 40482, 40478, 40481]) == ["<R>", "<s>", "</s>", "</R>"]
    assert mapper.text([40480, 40482, 40478, 40481]) == "."


def test_known_user_gets_hybrid_ranking_and_seen_items_are_removed(tmp_path):
    path = tmp_path / "model.npz"
    artifact(path)
    rows = HybridRanker(path).rank(
        10,
        [Candidate(100, 1.0), Candidate(200, 0.1), Candidate(300, 0.9)],
        collaborative_weight=0.8,
    )
    assert [row["recipe_id"] for row in rows] == [300, 200]
    assert rows[0]["source"] == "hybrid"


def test_cold_user_uses_content_and_popularity(tmp_path):
    path = tmp_path / "model.npz"
    artifact(path)
    rows = HybridRanker(path).rank(None, [Candidate(100, 0.1), Candidate(200, 0.9)])
    assert rows[0]["recipe_id"] == 200
    assert rows[0]["source"] == "cold_start"
