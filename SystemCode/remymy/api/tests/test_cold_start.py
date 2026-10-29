"""Cold start, before the model recommends for a user: the items rated by the most users, the ones their preferences
allow, the items they rated left out; items rated as often in a random order fixed per user."""
from __future__ import annotations

from datetime import datetime, timezone

from test_condition_bitmaps import food, make_index

from remymy_api.history import History
from remymy_api.inference.model_inference_score_recommend import RetrievalPipeline
from remymy_api.inference.service import Recommender
from remymy_api.schemas import UserRating

# 300 foods: every tenth of beef, the rest tofu; every third costs S$12, the rest S$4
RECIPES = [food(row, "Beef steak" if row % 10 == 0 else "Tofu, firm", "beef" if row % 10 == 0 else "tofu",
                12.0 if row % 3 == 0 else 4.0, 10) for row in range(300)]


def test_the_most_rated_rows_come_first():
    index = make_index(RECIPES, 300)
    counts = [0] * 300
    counts[7], counts[42], counts[99] = 5, 9, 5

    rows = index.cold_start_rows(3, counts=counts, seed=1)

    assert rows[0] == 42 and sorted(rows[1:]) == [7, 99]


def test_rows_rated_as_often_go_in_an_order_the_seed_fixes():
    index = make_index(RECIPES, 300)

    assert index.cold_start_rows(20, seed=1) == index.cold_start_rows(20, seed=1)
    assert index.cold_start_rows(20, seed=1) != index.cold_start_rows(20, seed=2)
    assert sorted(index.cold_start_rows(300, seed=1)) == list(range(300))


def test_only_the_rows_the_preferences_allow_come_back():
    index = make_index(RECIPES, 300)
    counts = [1 if row % 10 == 0 else 0 for row in range(300)]

    # the beef is the most rated, but a vegetarian gets none; nothing over S$5 either: every tofu at S$4
    rows = index.cold_start_rows(300, {"special_diet": "vegetarian", "budget": 5}, counts=counts, seed=1)

    assert sorted(rows) == [row for row in range(300) if row % 10 != 0 and row % 3 != 0]


def test_a_left_out_row_makes_way_for_the_next():
    index = make_index(RECIPES, 300)
    counts = [row % 4 for row in range(300)]
    first, *rest = index.cold_start_rows(6, counts=counts, seed=1)

    assert index.cold_start_rows(5, exclude={first}, counts=counts, seed=1) == rest


def test_each_user_gets_the_most_rated_without_what_they_rated():
    recommender = Recommender(pipeline=RetrievalPipeline(None, RECIPES, make_index(RECIPES, 300)))
    alice, bob = History(1), History(2)
    counts = {"5": 3, "8": 2, "11": 1}
    alice.add(UserRating(user_id=1, timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc), like=False, recipe_id="5"))

    picks = recommender.cold_start_items(bob, 3, rating_counts=counts)

    assert set(picks[0]) == {"fdc_id", "name", "price"}
    assert [pick["fdc_id"] for pick in picks] == [5, 8, 11]
    # the food alice rated, liked or not, is left out; an id the catalog does not hold counts for nothing
    assert [pick["fdc_id"] for pick in recommender.cold_start_items(alice, 2, rating_counts={**counts, "x": 9})] == [8, 11]
    # past the rated ones, each user has an order of their own
    assert recommender.cold_start_items(alice, 20) != recommender.cold_start_items(bob, 20)
