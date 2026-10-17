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
from .repository.recipe_repository import is_food_id, recipe_repository
from .schemas import (
    RecommendationResponse,
    RecommendedRecipe,
    UserBuyingProfile,
    UserHistory,
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


def buying_profile(history: History, recipes: Mapping[str, Recipe]) -> UserBuyingProfile | None:
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
    return UserBuyingProfile(
        user_id=history.user_id,
        avg_price=fmean(prices),
        avg_nutrient_values={name: fmean(values) for name, values in sorted(nutrients.items())},
    )


def user_history(rows: list[UserHistory]) -> History:
    """A user's likes as fetch_user_history reads them (newest first), as their History (oldest first)."""
    history = History(rows[0].user_id)
    for row in reversed(rows):
        history.add(row)
    return history


def digest_recipes(history: History) -> dict[str, Recipe]:
    """The Recipe of each recipe or catalog food in the history: the shared recipe_repository digests it once,
    however often it is in the history."""
    ids = list(dict.fromkeys(entry.recipe_id for entry in history))
    # a catalog food the user rated itself, such as a recommended grocery, is digested as itself
    recipe_ids = {recipe_id for recipe_id in ids if not is_food_id(recipe_id)}
    ingredients_by_recipe = recipe_ingredients(recipe_ids) if recipe_ids else {}
    recipes = {
        recipe_id: recipe_repository.digest_food(recipe_id) if is_food_id(recipe_id)
        else recipe_repository.digest(recipe_id, ingredients_by_recipe.get(recipe_id, []))
        for recipe_id in ids
    }
    logger.debug("User %d: %d ingredient lines of %d recipes matched %d priced foods.", history.user_id,
                 sum(map(len, ingredients_by_recipe.values())), len(recipes),
                 sum(len(recipe.ingredients) for recipe in recipes.values()))
    return recipes


def recommend(recommender: Recommender, rows: list[UserHistory]) -> RecommendationResponse:
    history = user_history(rows)
    recipes = digest_recipes(history)
    items = recommender.recommend(history, recipes, top_k=MAX_TOP_K)
    profile = buying_profile(history, recipes)
    if profile is None:
        logger.debug("User %d: no ingredient matched a priced food, so there is no buying profile.", history.user_id)
    recommended_items = sorted(items, key=lambda x: x["p_like"], reverse=True)
    return RecommendationResponse(
        profile=profile,
        items=[
            RecommendedRecipe(id=str(item["fdc_id"]), title=item["name"], score=item["p_like"])
            for item in recommended_items
        ],
    )
