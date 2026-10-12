from __future__ import annotations

from datetime import datetime, timezone

import pytest

from remymy_api.history import DEFAULT_MAX_ENTRIES, History, RecipeHistory
from remymy_api.recipe import Recipe
from remymy_api.schemas import Ingredient


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


def test_history_entries_refer_to_one_recipe():
    soup = Recipe("openrecipes:1", ["tomatoes"])
    first, again = RecipeHistory(soup, rating=4), RecipeHistory(soup, rating=5)
    assert first.recipe is again.recipe is soup


@pytest.mark.parametrize("rating", [-1, 6])
def test_ratings_outside_zero_to_five_are_rejected(rating):
    with pytest.raises(ValueError):
        RecipeHistory(Recipe("openrecipes:1", []), rating=rating)


def test_an_unrated_entry_is_stamped_now():
    entry = RecipeHistory(Recipe("openrecipes:1", []))
    assert entry.rating is None
    assert (datetime.now(timezone.utc) - entry.timestamp).total_seconds() < 5


def test_history_is_a_queue_that_drops_the_oldest_when_full():
    history = History(1, max_entries=2)
    recipes = [Recipe(f"openrecipes:{n}", []) for n in (1, 2, 3)]
    for recipe in recipes:
        history.add(RecipeHistory(recipe, rating=3))
    assert len(history) == 2
    assert [entry.recipe.recipe_id for entry in history] == ["openrecipes:2", "openrecipes:3"]
    assert History(2).history.maxlen == DEFAULT_MAX_ENTRIES
