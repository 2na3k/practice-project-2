from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from collections import defaultdict
from statistics import fmean

from fastapi import HTTPException

from .db import close_database, open_database
from .schemas import Ingredient, RecommendedRecipe, UserBuyingProfile, UserHistory


DEFAULT_ML_MODELS_URL = "http://ml-models:8003"


def ml_models_url() -> str:
    return os.getenv("ML_MODELS_URL", DEFAULT_ML_MODELS_URL).rstrip("/")


def call_ml_models(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        ml_models_url() + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail="Recommendations are unavailable.")


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
        raise HTTPException(status_code=503, detail="Recipe data is unavailable.")
    finally:
        close_database(connection)
    ingredients: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        ingredients[row["recipe_id"]].append(row["text"])
    return ingredients


def match_ingredients(texts: list[str]) -> dict[str, Ingredient]:
    if not texts:
        return {}
    matches = call_ml_models("/v1/ingredients/match", {"ingredients": texts})["matches"]
    return {text: Ingredient(**match) for text, match in zip(texts, matches) if match is not None}


def bought_ingredients(
    history: list[UserHistory],
    ingredients_by_recipe: dict[str, list[str]],
    matches: dict[str, Ingredient],
) -> list[tuple[UserHistory, Ingredient]]:
    bought = []
    for entry in history:
        for text in set(ingredients_by_recipe.get(entry.recipe_id, [])):
            ingredient = matches.get(text)
            if ingredient is None:
                continue
            bought.append((entry, ingredient))
    return bought


def buying_profile(bought: list[tuple[UserHistory, Ingredient]]) -> UserBuyingProfile:
    nutrients: dict[str, list[float]] = defaultdict(list)
    for _, ingredient in bought:
        for name, value in ingredient.nutrient_values.items():
            nutrients[name].append(value)
    return UserBuyingProfile(
        user_id=bought[0][0].user_id,
        avg_price=fmean(ingredient.price for _, ingredient in bought),
        avg_nutrient_values={name: fmean(values) for name, values in sorted(nutrients.items())},
    )


def recommend(bought: list[tuple[UserHistory, Ingredient]], top_k: int) -> list[RecommendedRecipe]:
    history = [
        {"fdc_id": ingredient.fdc_id, "rating": entry.rating, "date": entry.timestamp.date().isoformat()}
        for entry, ingredient in bought
    ]
    items = call_ml_models("/v1/recommend", {"history": history, "top_k": top_k})["items"]
    return [
        RecommendedRecipe(id=str(item["fdc_id"]), title=item["name"], score=item["p_like"])
        for item in items
    ]
