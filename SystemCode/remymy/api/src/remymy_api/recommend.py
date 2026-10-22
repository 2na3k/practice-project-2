from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from statistics import fmean
from typing import TYPE_CHECKING

from fastapi import HTTPException

from .db import close_database, open_database
from .history import History
from .recipe import Recipe
from .repository.recipe_repository import RecipeRepository, is_food_id
from .schemas import (
    RecommendationResponse,
    RecommendedRecipe,
    UserProfileInput,
    UserProfileOutput,
)

if TYPE_CHECKING:
    from .inference.service import Recommender

logger = logging.getLogger(__name__)

MAX_TOP_K = 50


def recipe_ingredients(recipe_ids: set[str]) -> dict[str, list[str]]:
    connection = open_database()
    try:
        placeholders = ",".join("?" for _ in recipe_ids)
        rows = connection.execute(
            "SELECT recipe_id, COALESCE(ingredient_text, raw_text) AS text FROM recipe_ingredient "
            f"WHERE recipe_id IN ({placeholders}) ORDER BY recipe_id, position",
            tuple(recipe_ids),
        ).fetchall()
    except sqlite3.Error:
        logger.exception("Reading the ingredient lines of %d history recipes failed.", len(recipe_ids))
        raise HTTPException(status_code=503, detail="Recipe data is unavailable.")
    finally:
        close_database(connection)
    ingredients: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        ingredients[row["recipe_id"]].append(row["text"])
    return ingredients


def buying_profile(history: History, recipes: Mapping[str, Recipe]) -> UserProfileOutput | None:
    """The average price and nutrient values of every priced food the user's recipes matched, a food once per time
    its recipe is in the history."""
    prices: list[float] = []
    nutrients: dict[str, list[float]] = defaultdict(list)
    for entry in history:
        for ingredient in recipes[entry.recipe_id].ingredients:
            prices.append(ingredient.price)
            for name, value in ingredient.nutrient_values.items():
                nutrients[name].append(value)
    if not prices:
        return None
    return UserProfileOutput(
        user_id=history.user_id,
        avg_price=fmean(prices),
        avg_nutrient_values={name: fmean(values) for name, values in sorted(nutrients.items())},
    )


def digest_recipes(history: History, catalog: RecipeRepository) -> dict[str, Recipe]:
    """The Recipe of each recipe or catalog food in the history: the catalog digests it once, however often it is in
    the history."""
    ids = list(dict.fromkeys(entry.recipe_id for entry in history))
    # a catalog food the user rated itself, such as a recommended grocery, is digested as itself
    recipe_ids = {recipe_id for recipe_id in ids if not is_food_id(recipe_id)}
    ingredients_by_recipe = recipe_ingredients(recipe_ids) if recipe_ids else {}
    recipes = {
        recipe_id: catalog.digest_food(recipe_id) if is_food_id(recipe_id)
        else catalog.digest(recipe_id, ingredients_by_recipe.get(recipe_id, []))
        for recipe_id in ids
    }
    logger.debug("User %d: %d ingredient lines of %d recipes matched %d priced foods.", history.user_id,
                 sum(map(len, ingredients_by_recipe.values())), len(recipes),
                 sum(len(recipe.ingredients) for recipe in recipes.values()))
    return recipes


def recommend(recommender: Recommender, user: UserProfileInput, catalog: RecipeRepository) -> RecommendationResponse:
    history = History.from_ratings(user.user_id, user.history)
    recipes = digest_recipes(history, catalog)
    # the preferences' diet and budget route the retrieval to the foods they allow
    recommended_items = recommender.recommend(history, recipes, top_k=MAX_TOP_K,
                                              preferences=user.preferences.model_dump())
    profile = buying_profile(history, recipes)
    if profile is None:
        logger.debug("User %d: no ingredient matched a priced food, so there is no buying profile.", history.user_id)

    budget = user.preferences.budget
    if budget and budget > 0:
        by_price = sorted(recommended_items, key=lambda item: item["price"])
        within_budget = 0
        for item in by_price:
            if item["price"] > budget:
                break
            within_budget += 1
        logger.debug("User %d: %d of %d recommended foods within the S$%.2f budget.", history.user_id,
                     within_budget, len(recommended_items), budget)
        recommended_items = by_price[:within_budget]

    # most likely to be liked first; of two as likely, the cheaper
    recommended_items = sorted(recommended_items, key=lambda item: (-item["p_like"], item["price"]))

    return RecommendationResponse(
        profile=profile,
        items=[
            RecommendedRecipe(id=str(item["fdc_id"]), title=item["name"], score=item["p_like"])
            for item in recommended_items
        ],
    )
