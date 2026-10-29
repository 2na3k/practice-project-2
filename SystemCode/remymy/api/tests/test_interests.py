"""Multi-interest retrieval: the liked history clustered into interests by cosine distance, the candidates split over
them, and one search per interest, routed once and never returning a row twice."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import torch
from test_condition_bitmaps import food, make_index

from remymy_api.inference.model_inference_score_recommend import (RetrievalPipeline, interest_clusters,
                                                                    split_candidates)


def unit(*rows: list[float]) -> np.ndarray:
    vectors = np.array(rows, dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def test_two_far_apart_tastes_are_two_interests_the_larger_first():
    # three recipes near x, two near z: a cosine distance of about 0.05 inside each, 1 between them
    history = unit([1, 0, 0], [0, 0, 1], [0.95, 0.31, 0], [0, 0.3, 0.95], [0.95, 0, 0.31])

    assert [members.tolist() for members in interest_clusters(history)] == [[0, 2, 4], [1, 3]]


def test_of_two_interests_as_large_the_more_recently_rated_comes_first():
    history = unit([1, 0, 0], [0, 0, 1])

    assert [members.tolist() for members in interest_clusters(history)] == [[1], [0]]


def test_a_recipe_rated_twice_weighs_twice():
    history = unit([0, 0, 1], [1, 0, 0], [1, 0, 0])

    assert [members.tolist() for members in interest_clusters(history)] == [[1, 2], [0]]


def test_one_recipe_is_one_interest():
    assert [members.tolist() for members in interest_clusters(unit([1, 2, 3]))] == [[0]]


def test_a_cut_of_two_keeps_every_recipe_in_one_interest():
    # the largest cosine distance: the normal approach, however far apart the recipes are
    history = unit([1, 0, 0], [0, 0, 1], [-1, 0, 0])

    assert [members.tolist() for members in interest_clusters(history, threshold=2)] == [[0, 1, 2]]


@pytest.mark.parametrize("sizes, n, expected", [([3, 1, 1], 100, [60, 20, 20]), ([2, 1], 10, [7, 3]),
                                                ([1, 1, 1], 2, [1, 1, 0]), ([5], 100, [100])])
def test_the_candidates_are_split_over_the_interests_by_size(sizes, n, expected):
    assert split_candidates(sizes, n) == expected


def test_each_interest_finds_its_own_candidates_none_twice():
    index = make_index(None, 300)
    queries = [index.embeddings[0], index.embeddings[0], index.embeddings[1]]

    found = index.search_many(queries, [20, 20, 10], exclude={0, 1})
    rows = [row for results in found for row, _ in results]

    assert [len(results) for results in found] == [20, 20, 10]
    assert len(set(rows)) == 50 and not {0, 1} & set(rows)
    # the second query, the same as the first, gets the next 20 nearest
    assert min(similarity for _, similarity in found[0]) >= max(similarity for _, similarity in found[1])


def test_the_constraints_are_routed_once_for_every_interest(monkeypatch):
    recipes = [food(row, "Beef steak" if row % 10 == 0 else "Tofu, firm", "beef" if row % 10 == 0 else "tofu",
                    4.0, 10) for row in range(300)]
    index = make_index(recipes, 300)
    routes = []
    route = index.route
    monkeypatch.setattr(index, "route", lambda *args: routes.append(args) or route(*args))

    found = index.search_many([index.embeddings[3], index.embeddings[4]], [30, 30],
                              conditions={"special_diet": "vegetarian"}, exclude={3, 4})
    rows = [row for results in found for row, _ in results]

    assert len(routes) == 1
    assert len(rows) == len(set(rows)) == 60
    assert all(row % 10 != 0 for row in rows) and not {3, 4} & set(rows)


def pipeline_stub() -> RetrievalPipeline:
    """A pipeline with only what interests() reads: the reranker's positive_min_rating."""
    pipeline = RetrievalPipeline.__new__(RetrievalPipeline)
    pipeline.reranker = SimpleNamespace(config={"positive_min_rating": 3})
    return pipeline


def test_only_the_liked_recipes_make_interests():
    history = [{"rating": 5}, {"rating": 0}, {"rating": 3}, {}]
    history_emb = torch.from_numpy(unit([1, 0, 0], [0, 1, 0], [0.95, 0.31, 0], [0, 0, 1]))

    interests = pipeline_stub().interests(history, history_emb)

    # the disliked recipe (rating 0) makes none; the one without a rating counts as liked
    assert [members.tolist() for members, _ in interests] == [[0, 2], [3]]
    assert torch.allclose(interests[0][1], history_emb[[0, 2]].mean(0))


def test_the_normal_approach_is_one_mean_of_the_liked_recipes_only():
    history = [{"rating": 5}, {"rating": 0}, {"rating": 4}, {"rating": 1}]
    history_emb = torch.from_numpy(unit([1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 1, 1]))

    interests = pipeline_stub().interests(history, history_emb, threshold=2)

    assert [members.tolist() for members, _ in interests] == [[0, 2]]
    assert torch.allclose(interests[0][1], history_emb[[0, 2]].mean(0))


def test_a_user_who_liked_nothing_has_no_interest_to_search_by():
    history = [{"rating": 1}, {"rating": 0}]
    history_emb = torch.from_numpy(unit([1, 0, 0], [0.95, 0.31, 0]))

    assert pipeline_stub().interests(history, history_emb) == []
    assert pipeline_stub().interests(history, history_emb, threshold=2) == []
