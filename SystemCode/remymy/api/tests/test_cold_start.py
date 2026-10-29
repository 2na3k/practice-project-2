"""Cold start, before the model recommends for a user: random items in an order fixed per user, the ones their
preferences allow, the items they rated left out."""
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


def test_the_same_seed_gives_the_same_order_and_another_seed_another():
    index = make_index(RECIPES, 300)

    assert index.random_rows(20, seed=1) == index.random_rows(20, seed=1)
    assert index.random_rows(20, seed=1) != index.random_rows(20, seed=2)
    assert sorted(index.random_rows(300, seed=1)) == list(range(300))


def test_only_the_rows_the_preferences_allow_come_back():
    index = make_index(RECIPES, 300)

    rows = index.random_rows(300, {"special_diet": "vegetarian", "budget": 5}, seed=1)

    # no beef, and nothing over S$5: every tofu at S$4
    assert sorted(rows) == [row for row in range(300) if row % 10 != 0 and row % 3 != 0]


def test_a_left_out_row_makes_way_for_the_next_in_the_order():
    index = make_index(RECIPES, 300)
    first, *rest = index.random_rows(6, seed=1)

    assert index.random_rows(5, exclude={first}, seed=1) == rest
    assert index.random_rows(5, {"special_diet": "vegetarian"}, exclude={first}, seed=1) == [
        row for row in index.random_rows(6, {"special_diet": "vegetarian"}, seed=1) if row != first][:5]


def test_each_user_has_an_order_of_their_own_without_what_they_rated():
    recommender = Recommender(pipeline=RetrievalPipeline(None, RECIPES, make_index(RECIPES, 300)))
    alice, bob = History(1), History(2)
    picks = recommender.random_items(alice, 5)
    alice.add(UserRating(user_id=1, timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc), like=False,
                         recipe_id=str(picks[0]["fdc_id"])))

    assert set(picks[0]) == {"fdc_id", "name", "price"}
    assert recommender.random_items(bob, 5) != picks
    # the rated food, liked or not, is left out, and the next in alice's order takes its place
    assert recommender.random_items(alice, 4) == picks[1:]
