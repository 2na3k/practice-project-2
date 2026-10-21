"""The cluster-first routing of recommender.MD: the foods' tags, their macro-clusters by Jaccard distance, the routing
of a query's constraints through them, and the two ways a routed search runs (masked HNSW, the exact scan)."""
from __future__ import annotations

import hnswlib
import numpy as np
import pytest
from pyroaring import BitMap

from remymy_api.inference import model_inference_score_recommend as pipeline
from remymy_api.inference.model_inference_score_recommend import (
    CATALOG_NUTRIENTS, RecipeIndex, average_linkage, condition_bitmaps, food_tags, jaccard_distances, macro_clusters,
    nutrient_key, preference_conditions, thread_groups, ThreadExecutor)


def food(fdc_id: int, name: str, product: str, price: float, protein: float) -> dict:
    """A catalog recipe in the reranker's input format, as inference.service.catalog_recipes makes it."""
    return {"fdc_id": fdc_id, "name": name, "price": price,
            "nutrients": {key: 1.0 for key in CATALOG_NUTRIENTS} | {"protein_g": protein},
            "entities": {"product": [[product]], "adj": [], "verb": []}}


RECIPES = [food(1, "Pork belly", "pork", 8, 20), food(2, "Tofu, firm", "tofu", 3, 15),
           food(3, "Beef steak", "beef", 15, 30), food(4, "Salmon fillet", "salmon", 12, 25),
           food(5, "Lentils", "lentil", 2, 9), food(6, "Rice", "rice", 1, 3)]


def make_index(recipes: list[dict] | None, n: int, seed: int = 0) -> RecipeIndex:
    embeddings = np.random.default_rng(seed).normal(size=(n, 8)).astype(np.float32)
    index = hnswlib.Index(space="cosine", dim=8)
    index.init_index(max_elements=n, M=16, ef_construction=200, random_seed=seed)
    index.add_items(embeddings, np.arange(n))
    return RecipeIndex(list(range(n)) if recipes is None else [recipe["fdc_id"] for recipe in recipes], embeddings,
                       index, recipes)


@pytest.fixture
def recipe_index() -> RecipeIndex:
    return make_index(RECIPES, len(RECIPES))


def rows(results) -> list[int]:
    return sorted(row for row, _ in results)


def test_each_condition_has_a_bitmap_of_which_rows_meet_it():
    bitmaps = condition_bitmaps(RECIPES)

    assert bitmaps["diet:vegetarian"].astype(int).tolist() == [0, 1, 0, 0, 1, 1]
    assert bitmaps["diet:halal"].astype(int).tolist() == [0, 1, 1, 1, 1, 1]
    assert bitmaps["diet:Hinduism"].astype(int).tolist() == [1, 1, 0, 1, 1, 1]
    # the top quarter of the catalog's protein
    assert bitmaps["nutrient:protein_g"].astype(int).tolist() == [0, 0, 1, 1, 0, 0]


def test_preferences_name_their_conditions():
    assert nutrient_key("Protein") == "protein_g"
    assert nutrient_key("vitamin c") is None
    # only the diet filters (the budget is checked apart); the preferred nutrient does not, for now
    assert preference_conditions({"special_diet": "halal", "preferred_nutrient": "protein", "budget": 10}) == [
        "diet:halal"]
    assert preference_conditions({"preferred_nutrient": "protein"}) == []
    assert preference_conditions({}) == []


def test_each_food_carries_a_tag_for_each_diet_it_passes():
    tags, matrix = food_tags(RECIPES)
    carried = {RECIPES[row]["name"]: {tag for tag, has in zip(tags, matrix[row]) if has} for row in range(len(RECIPES))}

    # only the three diets a user can set: no price or nutrient tags
    assert tags == ["diet:vegetarian", "diet:halal", "diet:Hinduism"]
    assert carried["Tofu, firm"] == {"diet:vegetarian", "diet:halal", "diet:Hinduism"}
    assert carried["Beef steak"] == {"diet:halal"}
    assert carried["Pork belly"] == {"diet:Hinduism"}


def test_a_budget_keeps_one_stretch_at_the_start_of_each_group(recipe_index):
    stored = recipe_index.order.tolist()
    for cluster in recipe_index.clusters:
        for _, group_rows in cluster.groups:
            positions = sorted(stored.index(row) for row in group_rows)
            prices = [recipe_index.prices[stored[position]] for position in positions]
            # cheapest first: any budget keeps a prefix of the group
            assert prices == sorted(prices)


def test_jaccard_distance_is_one_minus_the_shared_share_of_both():
    sets = np.array([[1, 1, 0], [0, 1, 1], [1, 1, 0], [0, 0, 0], [0, 0, 0]], dtype=bool)
    distance = jaccard_distances(sets)

    assert distance[0, 1] == pytest.approx(1 - 1 / 3)
    assert distance[0, 2] == 0  # the same set
    assert distance[3, 4] == 0  # two empty sets
    assert distance[0, 3] == 1  # nothing shared


def test_a_weighted_row_clusters_as_that_many_duplicates():
    points = np.array([0.0, 1.0, 5.0, 5.5])
    sizes = np.array([3, 1, 2, 1])
    weighted = average_linkage(np.abs(points[:, None] - points[None, :]), threshold=2.0, sizes=sizes)
    expanded_points = np.repeat(points, sizes)
    expanded = average_linkage(np.abs(expanded_points[:, None] - expanded_points[None, :]), threshold=2.0)

    assert weighted.tolist() == [0, 0, 1, 1]
    assert expanded.tolist() == np.repeat(weighted, sizes).tolist()


def test_a_merge_exactly_at_the_threshold_is_kept():
    # 1 - 3/5 is 0.4 up to float rounding: the two sets still merge at a 0.4 threshold
    sets = np.array([[1, 1, 1, 0, 0], [1, 1, 1, 1, 1]], dtype=bool)

    assert len(set(average_linkage(jaccard_distances(sets), threshold=0.4))) == 1


def test_macro_clusters_group_overlapping_tag_sets():
    # tags a, b, c, d: {a, b} x3 and {a, b, c} x2 overlap (distance 1/3), {c, d} x2 shares little with either
    matrix = np.array([[1, 1, 0, 0]] * 3 + [[1, 1, 1, 0]] * 2 + [[0, 0, 1, 1]] * 2, dtype=bool)
    clusters = sorted(macro_clusters(matrix, threshold=0.4), key=lambda cluster: min(cluster.members))

    assert [list(cluster.members) for cluster in clusters] == [[0, 1, 2, 3, 4], [5, 6]]
    # every member carries a and b, two of the five carry c, none d
    assert clusters[0].centroid.tolist() == pytest.approx([1, 1, 0.4, 0])
    assert sorted(len(rows_) for _, rows_ in clusters[0].groups) == [2, 3]


def test_routing_finds_exactly_the_rows_the_constraints_allow(recipe_index):
    assert recipe_index.route({}) is None
    assert recipe_index.route({"budget": 0}) is None

    matching, plan = recipe_index.route({"special_diet": "vegetarian"})
    assert list(matching) == [1, 4, 5]
    assert sum(plan.values()) == len(recipe_index.clusters)
    # the S$3 band holds the tofu at S$3, the exact check leaves it out of a S$2.50 budget
    assert list(recipe_index.route({"budget": 2.5})[0]) == [4, 5]
    assert list(recipe_index.route({"special_diet": "halal", "budget": 10}, exclude={1})[0]) == [4, 5]
    # a budget above every band is checked exactly too
    assert list(recipe_index.route({"budget": 100})[0]) == [0, 1, 2, 3, 4, 5]
    # a preferred nutrient does not narrow the rows, and on its own it does not route at all
    assert list(recipe_index.route({"special_diet": "vegetarian", "preferred_nutrient": "protein"})[0]) == [1, 4, 5]
    assert recipe_index.route({"preferred_nutrient": "protein"}) is None


def test_search_returns_only_the_rows_the_preferences_allow(recipe_index):
    query = recipe_index.embeddings[0]

    assert rows(recipe_index.search(query, 6)) == [0, 1, 2, 3, 4, 5]
    assert rows(recipe_index.search(query, 6, conditions={"special_diet": "vegetarian"})) == [1, 4, 5]
    # halal leaves out the pork; the S$10 budget the beef and the salmon
    assert rows(recipe_index.search(query, 6, conditions={"special_diet": "halal", "budget": 10})) == [1, 4, 5]
    assert rows(recipe_index.search(query, 6, conditions={"special_diet": "vegetarian"}, exclude={1})) == [4, 5]
    assert recipe_index.search(query, 6, conditions={"budget": 0.5}) == []
    # a budget of 0 is no limit
    assert rows(recipe_index.search(query, 6, conditions={"budget": 0})) == [0, 1, 2, 3, 4, 5]


def test_a_broad_match_searches_hnsw_through_a_mask_and_a_narrow_one_is_scanned():
    # 300 foods: 30 of beef, the rest tofu; 9 of them (3%) cost under S$1
    recipes = [food(row, "Beef steak" if row % 10 == 0 else "Tofu, firm", "beef" if row % 10 == 0 else "tofu",
                    0.5 if row % 33 == 1 else 4.0, 10) for row in range(300)]
    index = make_index(recipes, 300)
    unit = index.embeddings / np.linalg.norm(index.embeddings, axis=1, keepdims=True)
    query = unit[7]

    logged: list[str] = []
    broad = index.search(query, 20, conditions={"special_diet": "vegetarian"}, log=logged.append)
    assert "HNSW through a bitset mask" in logged[-1]
    assert all(row % 10 != 0 for row, _ in broad) and len(broad) == 20

    narrow = index.search(query, 5, conditions={"budget": 1}, log=logged.append)
    assert "exact scan" in logged[-1]
    cheap = np.array([row for row in range(300) if row % 33 == 1])
    exact = cheap[np.argsort(-(unit[cheap] @ query))][:5]
    assert [row for row, _ in narrow] == exact.tolist()


@pytest.mark.parametrize("threads", [4, 1])
def test_the_scan_is_exact_across_buckets(monkeypatch, threads):
    monkeypatch.setattr(pipeline, "SCAN_THREADS", threads)
    monkeypatch.setattr(pipeline, "SCAN_BUCKET", 20)
    index = make_index(None, 500, seed=3)
    unit = index.embeddings / np.linalg.norm(index.embeddings, axis=1, keepdims=True)
    candidates = np.sort(np.random.default_rng(4).choice(500, 150, replace=False))  # 8 buckets of 20
    query = np.random.default_rng(5).normal(size=8).astype(np.float32)

    found = index.scan(query, BitMap(candidates.tolist()), 10)
    similarity = unit[candidates] @ (query / np.linalg.norm(query))
    order = np.argsort(-similarity)[:10]
    assert [row for row, _ in found] == candidates[order].tolist()
    assert [score for _, score in found] == pytest.approx(similarity[order].tolist(), abs=1e-5)


def test_the_scan_reads_a_structure_of_arrays():
    index = make_index(None, 50)
    unit = index.embeddings / np.linalg.norm(index.embeddings, axis=1, keepdims=True)

    # one contiguous array per dimension, holding that dimension for every row
    assert index.columns.shape == (8, 50) and index.columns.flags["C_CONTIGUOUS"]
    assert index.columns[3] == pytest.approx(unit[:, 3])


def test_the_leaf_order_keeps_every_sub_cluster_contiguous():
    # two pairs of near sets far from each other, and the pairs' own two members nearest of all
    sets = np.array([[1, 1, 1, 0, 0, 0, 0], [0, 0, 0, 1, 1, 1, 1], [1, 1, 0, 0, 0, 0, 0], [0, 0, 0, 1, 1, 1, 0]],
                    dtype=bool)
    labels, order = average_linkage(jaccard_distances(sets), threshold=0.5, leaf_order=True)

    assert sorted(order.tolist()) == [0, 1, 2, 3]
    assert labels[0] == labels[2] and labels[1] == labels[3] and labels[0] != labels[1]
    # each cluster's rows sit next to each other in the order
    position = {row: index for index, row in enumerate(order.tolist())}
    assert abs(position[0] - position[2]) == 1 and abs(position[1] - position[3]) == 1


def test_the_columns_store_each_tag_set_group_contiguously(recipe_index):
    stored = recipe_index.order.tolist()
    for cluster in recipe_index.clusters:
        for _, group_rows in cluster.groups:
            positions = sorted(stored.index(row) for row in group_rows)
            assert positions == list(range(positions[0], positions[0] + len(positions)))


def test_each_thread_owns_a_contiguous_group_of_buckets():
    # 6 buckets on 3 threads: two neighbours each, not round-robin
    assert thread_groups(6, 3) == [[0, 1], [2, 3], [4, 5]]
    # 9 buckets on 4 threads: 9 // 4 each, and the 9 % 4 first groups one more
    assert thread_groups(9, 4) == [[0, 1, 2], [3, 4], [5, 6], [7, 8]]
    # fewer buckets than threads: a group, and a thread, per bucket
    assert thread_groups(2, 4) == [[0], [1]]
    assert thread_groups(5, 1) == [[0, 1, 2, 3, 4]]


def test_a_segment_makes_its_full_buckets_and_one_smaller_last_bucket(monkeypatch):
    # 2,450 rows stored side by side: 2,450 // 1,000 = 2 full buckets and the 450 left one smaller bucket
    monkeypatch.setattr(pipeline, "SCAN_BUCKET", 1000)
    index = make_index(None, 3000, seed=6)
    groups_seen = []
    monkeypatch.setattr(pipeline, "thread_groups",
                        lambda num_buckets, num_threads: groups_seen.append(num_buckets) or thread_groups(num_buckets,
                                                                                                          num_threads))
    query = index.embeddings[0]
    found = index.scan(query, BitMap(range(100, 2550)), 5)

    assert groups_seen == [3]
    unit = index.embeddings / np.linalg.norm(index.embeddings, axis=1, keepdims=True)
    exact = 100 + np.argsort(-(unit[100:2550] @ (query / np.linalg.norm(query))))[:5]
    assert [row for row, _ in found] == exact.tolist()


def test_the_last_long_runs_remainder_shares_buckets_with_the_leftover_rows(monkeypatch):
    # rows 0-2,449 stored side by side (2 full buckets and 450 more) and 5 scattered ones: the 450 join the 5
    monkeypatch.setattr(pipeline, "SCAN_BUCKET", 1000)
    index = make_index(None, 4000, seed=7)
    bucket_counts = []
    monkeypatch.setattr(pipeline, "thread_groups", lambda num_buckets, num_threads: bucket_counts.append(num_buckets)
                        or thread_groups(num_buckets, num_threads))
    scattered = [2600, 2900, 3100, 3500, 3900]
    rows = list(range(2450)) + scattered
    query = index.embeddings[1]
    found = index.scan(query, BitMap(rows), 10)

    # two full buckets read in place, then one of 450 + 5 rows, rather than a bucket of 450 and another of 5
    assert bucket_counts == [3]
    unit = index.embeddings / np.linalg.norm(index.embeddings, axis=1, keepdims=True)
    candidates = np.array(rows)
    exact = candidates[np.argsort(-(unit[candidates] @ (query / np.linalg.norm(query))))[:10]]
    assert [row for row, _ in found] == exact.tolist()


def test_the_food_datas_diet_columns_win_over_the_word_screen():
    # the tofu's data says it is not halal, the beef's says it is fit for Hinduism: the columns decide; the rice has no
    # values, so its words are screened
    recipes = [food(1, "Tofu, firm", "tofu", 3, 15)
               | {"special_diet": {"vegetarian": True, "halal": False, "hinduism": True}},
               food(2, "Beef steak", "beef", 15, 30)
               | {"special_diet": {"vegetarian": False, "halal": True, "hinduism": True}},
               food(3, "Rice", "rice", 1, 3) | {"special_diet": {"vegetarian": None, "halal": None, "hinduism": None}}]
    bitmaps = condition_bitmaps(recipes)

    assert bitmaps["diet:vegetarian"].tolist() == [True, False, True]
    assert bitmaps["diet:halal"].tolist() == [False, True, True]
    assert bitmaps["diet:Hinduism"].tolist() == [True, True, True]


def test_the_executor_runs_every_bucket_once_and_can_run_again():
    import threading as threads

    executor = ThreadExecutor(4)
    for _ in range(2):  # a second run on the same sub-threads: the semaphore and queues are back to free
        seen, lock = [], threads.Lock()

        def work(bucket):
            with lock:
                seen.append(bucket)

        ran_by = executor.run(500, work)
        assert sorted(seen) == list(range(500))
        assert set(ran_by) <= {0, 1, 2, 3} and None not in ran_by


def test_a_free_sub_thread_gets_buckets_redirected_from_the_back_of_the_busiest():
    import time as clock

    executor = ThreadExecutor(4)
    # 40 buckets, groups of 10; sub-thread 0's buckets are slow, the others' instant, so the others put 0 early
    ran_by = executor.run(40, lambda bucket: clock.sleep(0.01) if bucket < 10 else None)

    # every bucket ran; sub-thread 0 started at the front of its group, and free sub-threads took over part of it
    # (redirected buckets can move again, even back to sub-thread 0 once it is free, so no exact split is fixed)
    assert None not in ran_by
    assert ran_by[0] == 0
    assert any(ran_by[bucket] != 0 for bucket in range(10))


def test_an_error_in_a_bucket_is_raised_and_the_executor_still_runs():
    executor = ThreadExecutor(4)

    def work(bucket):
        if bucket == 25:
            raise ValueError("bucket 25 failed")

    with pytest.raises(ValueError, match="bucket 25 failed"):
        executor.run(40, work)
    assert len(executor.run(8, lambda bucket: None)) == 8


def test_fewer_buckets_than_sub_threads():
    # 2 buckets on 4 sub-threads: the two with no group start free, so a bucket can be redirected to them at once
    ran_by = ThreadExecutor(4).run(2, lambda bucket: None)
    assert len(ran_by) == 2 and None not in ran_by
