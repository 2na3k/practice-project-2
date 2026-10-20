from __future__ import annotations

from datetime import datetime, timezone

from remymy_api.history import DEFAULT_MAX_ENTRIES, History
from remymy_api.recipe import Recipe
from remymy_api.schemas import Ingredient, UserRating


def test_a_recipe_keeps_its_own_copy_of_ingredients_and_nutrients():
    tomatoes = Ingredient(fdc_id=1, name="Tomatoes, red, ripe", price=2.0, nutrient_values={"energy_kcal": 18.0})
    ingredients = [tomatoes]
    nutrients = {"energy_kcal": 18.0}
    soup = Recipe("openrecipes:1", ingredients, nutrients)
    ingredients.append(Ingredient(fdc_id=2, name="Salt, table", price=1.0, nutrient_values={}))
    nutrients["energy_kcal"] = 0.0
    assert soup.ingredients == [tomatoes]
    assert soup.nutrient_values == {"energy_kcal": 18.0}
    assert Recipe("openrecipes:2", []).nutrient_values == {}


def like(recipe_id: str, liked: bool = True) -> UserRating:
    return UserRating(user_id=1, timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc), like=liked, recipe_id=recipe_id)


def test_history_is_a_queue_that_drops_the_oldest_when_full():
    history = History(1, max_entries=2)
    for recipe_id in ("openrecipes:1", "openrecipes:2", "openrecipes:3"):
        history.add(like(recipe_id))
    assert len(history) == 2
    assert [entry.recipe_id for entry in history] == ["openrecipes:2", "openrecipes:3"]
    assert History(2).history.maxlen == DEFAULT_MAX_ENTRIES


def test_history_keeps_each_entrys_like():
    history = History(1)
    history.add(like("openrecipes:1", liked=False))
    history.add(like("openrecipes:1"))
    assert [entry.like for entry in history] == [False, True]


def test_history_from_ratings_keeps_their_order_and_the_newest_when_full():
    ratings = [like("openrecipes:1"), like("openrecipes:2", liked=False), like("openrecipes:3")]

    history = History.from_ratings(1, ratings)
    assert history.user_id == 1
    assert [(entry.recipe_id, entry.like) for entry in history] == [
        ("openrecipes:1", True), ("openrecipes:2", False), ("openrecipes:3", True)]

    assert [entry.recipe_id for entry in History.from_ratings(1, ratings, max_entries=2)] == ["openrecipes:2", "openrecipes:3"]
    assert len(History.from_ratings(1, [])) == 0
