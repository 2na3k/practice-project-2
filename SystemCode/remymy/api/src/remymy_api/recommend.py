from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from statistics import fmean
from typing import TYPE_CHECKING

from fastapi import HTTPException

from .db import close_database, open_database
from .history import History, RecipeHistory
from .repository.recipe_repository import recipe_repository
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


def buying_profile(history: History) -> UserBuyingProfile | None:
    """The average price and nutrient values of every priced food the user's rated recipes matched, a food once per
    rating of its recipe."""
    prices: list[float] = []
    nutrients: dict[str, list[float]] = defaultdict(list)
    for entry in history:
        for ingredient in entry.recipe.ingredients:
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
    """A user's ratings, as fetch_user_history reads them (newest first), as their History (oldest first). The shared
    recipe_repository digests each recipe once, so the ratings of a recipe rated twice refer to one Recipe."""
    ingredients_by_recipe = recipe_ingredients({row.recipe_id for row in rows})
    recipes = {
        recipe_id: recipe_repository.digest(recipe_id, ingredients_by_recipe.get(recipe_id, []))
        for recipe_id in dict.fromkeys(row.recipe_id for row in rows)
    }
    history = History(rows[0].user_id)
    for row in reversed(rows):
        history.add(RecipeHistory(recipes[row.recipe_id], row.rating, row.timestamp))
    logger.debug("User %d: %d ingredient lines of %d recipes matched %d priced foods.", history.user_id,
                 sum(map(len, ingredients_by_recipe.values())), len(recipes),
                 sum(len(recipe.ingredients) for recipe in recipes.values()))
    return history


def recommend(recommender: Recommender, rows: list[UserHistory]) -> RecommendationResponse:
    history = user_history(rows)
    items = recommender.recommend(history, top_k=MAX_TOP_K)
    profile = buying_profile(history)
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
