from __future__ import annotations

import hnswlib
import numpy as np
import pytest

from remymy_api.inference.model_inference_score_recommend import (
    CATALOG_NUTRIENTS, RecipeIndex, condition_bitmaps, nutrient_key, preference_conditions)


def food(fdc_id: int, name: str, product: str, price: float, protein: float) -> dict:
    """A catalog recipe in the reranker's input format, as inference.service.catalog_recipes makes it."""
    return {"fdc_id": fdc_id, "name": name, "price": price,
            "nutrients": {key: 1.0 for key in CATALOG_NUTRIENTS} | {"protein_g": protein},
            "entities": {"product": [[product]], "adj": [], "verb": []}}


RECIPES = [food(1, "Pork belly", "pork", 8, 20), food(2, "Tofu, firm", "tofu", 3, 15),
           food(3, "Beef steak", "beef", 15, 30), food(4, "Salmon fillet", "salmon", 12, 25),
           food(5, "Lentils", "lentil", 2, 9), food(6, "Rice", "rice", 1, 3)]


@pytest.fixture
def recipe_index() -> RecipeIndex:
    embeddings = np.random.default_rng(0).normal(size=(len(RECIPES), 8)).astype(np.float32)
    index = hnswlib.Index(space="cosine", dim=8)
    index.init_index(max_elements=len(RECIPES), M=16, ef_construction=200)
    index.add_items(embeddings, np.arange(len(RECIPES)))
    return RecipeIndex([recipe["fdc_id"] for recipe in RECIPES], embeddings, index, RECIPES)


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
    assert preference_conditions({"special_diet": "halal", "preferred_nutrient": "protein", "budget": 10}) == [
        "diet:halal", "nutrient:protein_g"]
    assert preference_conditions({}) == []


def rows(results) -> list[int]:
    return sorted(row for row, _ in results)


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
