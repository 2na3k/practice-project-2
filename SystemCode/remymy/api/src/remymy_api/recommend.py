from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from statistics import fmean
from typing import TYPE_CHECKING

from fastapi import HTTPException

from .db import close_database, open_database
from .schemas import (
    Ingredient,
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


def buying_profile(
    history: list[UserHistory], matched: list[list[Ingredient]]
) -> UserBuyingProfile | None:
    prices: list[float] = []
    nutrients: dict[str, list[float]] = defaultdict(list)
    for ingredients in matched:
        for ingredient in ingredients:
            prices.append(ingredient.price)
            for name, value in ingredient.nutrient_values.items():
                nutrients[name].append(value)
    if not prices:
        return None
    return UserBuyingProfile(
        user_id=history[0].user_id,
        avg_price=fmean(prices),
        avg_nutrient_values={name: fmean(values) for name, values in sorted(nutrients.items())},
    )


def recommend(recommender: Recommender, history: list[UserHistory]) -> RecommendationResponse:
    ingredients_by_recipe = recipe_ingredients({entry.recipe_id for entry in history})
    result = recommender.recommend(
        [
            {
                "rating": entry.rating,
                "date": entry.timestamp.date().isoformat(),
                "ingredients": ingredients_by_recipe.get(entry.recipe_id, []),
            }
            for entry in history
        ],
        top_k=MAX_TOP_K,
    )
    matched = [[Ingredient(**ingredient) for ingredient in entry] for entry in result["ingredients"]]
    logger.debug("User %d: %d ingredient lines of %d recipes matched %d priced foods.", history[0].user_id,
                 sum(map(len, ingredients_by_recipe.values())), len(ingredients_by_recipe), sum(map(len, matched)))
    if not any(matched):
        logger.debug("User %d: no ingredient matched a priced food, so there is no buying profile.",
                     history[0].user_id)
    recommended_items = sorted(result["items"], key=lambda x: x["p_like"], reverse=True)
    return RecommendationResponse(
        profile=buying_profile(history, matched),
        items=[
            RecommendedRecipe(id=str(item["fdc_id"]), title=item["name"], score=item["p_like"])
            for item in recommended_items
        ],
    )
