from __future__ import annotations

import logging
import sqlite3
import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request

from .db import close_database, open_database, split_preview
from .cache import drop_recommendation, read_recommendations, write_recommendations
from .recommend import recommend
from .schemas import (
    HealthResponse,
    Preferences,
    RecipeDetail,
    RecipeListResponse,
    RecipeSummary,
    RecommendationResponse,
    User,
    UserHistory,
)
from .users import add_history, fetch_user_history, fetch_user_likes, open_user_database

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Report process health; recipe routes report data availability."""

    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse, tags=["health"])
def ready() -> HealthResponse:
    """Report readiness only after the normalized recipe database is usable."""

    connection = open_database()
    close_database(connection)
    return HealthResponse(status="ok")


@router.get("/api/recipes", response_model=RecipeListResponse, tags=["recipes"])
def list_recipes(
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RecipeListResponse:
    connection = open_database()
    try:
        clauses: list[str] = []
        params: list[object] = []
        search = q.strip() if q else ""
        if search:
            pattern = f"%{search}%"
            clauses.append(
                "(r.title LIKE ? COLLATE NOCASE OR EXISTS ("
                "SELECT 1 FROM recipe_ingredient i2 "
                "WHERE i2.recipe_id = r.recipe_id "
                "AND (i2.raw_text LIKE ? COLLATE NOCASE OR i2.ingredient_text LIKE ? COLLATE NOCASE)"
                "))"
            )
            params.extend([pattern, pattern, pattern])
        where = " WHERE r.title IS NOT NULL AND trim(r.title) <> ''"
        if clauses:
            where += " AND " + " AND ".join(clauses)
        total = connection.execute(
            f"SELECT COUNT(*) FROM recipe r{where}", tuple(params)
        ).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT
              r.recipe_id AS id,
              r.title AS title,
              r.quality_status AS quality_status,
              (SELECT COUNT(*) FROM recipe_ingredient i WHERE i.recipe_id = r.recipe_id) AS ingredient_count,
              COALESCE((SELECT GROUP_CONCAT(raw_text, char(10)) FROM (
                SELECT raw_text FROM recipe_ingredient i3
                WHERE i3.recipe_id = r.recipe_id ORDER BY i3.position LIMIT 3
              )), '') AS ingredient_preview
            FROM recipe r
            {where}
            ORDER BY r.recipe_id
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (limit, offset),
        ).fetchall()
        items = [
            RecipeSummary(
                id=row["id"],
                title=row["title"],
                ingredient_count=row["ingredient_count"],
                ingredient_preview=split_preview(row["ingredient_preview"]),
                quality_status=row["quality_status"],
            )
            for row in rows
        ]
        return RecipeListResponse(items=items, total=total, limit=limit, offset=offset)
    except HTTPException:
        raise
    except sqlite3.Error:
        logger.exception("Recipe search failed (q=%r, limit=%d, offset=%d).", q, limit, offset)
        raise HTTPException(status_code=503, detail="Recipe data is unavailable.")
    finally:
        close_database(connection)


@router.get("/api/recipes/{recipe_id}", response_model=RecipeDetail, tags=["recipes"])
def get_recipe(recipe_id: str) -> RecipeDetail:
    connection = open_database()
    try:
        row = connection.execute(
            "SELECT recipe_id, title, quality_status FROM recipe "
            "WHERE recipe_id = ? AND title IS NOT NULL AND trim(title) <> ''",
            (recipe_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Recipe not found.")
        ingredients = [
            item[0]
            for item in connection.execute(
                "SELECT raw_text FROM recipe_ingredient WHERE recipe_id = ? ORDER BY position",
                (recipe_id,),
            ).fetchall()
        ]
        instructions = [
            item[0]
            for item in connection.execute(
                "SELECT text FROM recipe_instruction WHERE recipe_id = ? ORDER BY position",
                (recipe_id,),
            ).fetchall()
        ]
        return RecipeDetail(
            id=row["recipe_id"],
            title=row["title"],
            ingredients=ingredients,
            instructions=instructions,
            quality_status=row["quality_status"],
        )
    except HTTPException:
        raise
    except sqlite3.Error:
        logger.exception("Reading recipe %s failed.", recipe_id)
        raise HTTPException(status_code=503, detail="Recipe data is unavailable.")
    finally:
        close_database(connection)

@router.get("/api/recommend/{username}", response_model=RecommendationResponse, tags=["recommendations"])
def get_recommendation(
    username: Annotated[
        str,
        Path(openapi_examples={"demo": {"summary": "Seeded demo user", "value": "demo"}}),
    ],
    request: Request,
    top_k: Annotated[int, Query(ge=1, le=50)] = 10,
) -> RecommendationResponse:
    cache = request.app.state.cache
    connection = open_user_database()
    try:
        user_history = fetch_user_history(connection, cache, username)
    finally:
        connection.close()
    if len(user_history) == 0:
        logger.debug("No history for %r, so no recommendations.", username)
        return RecommendationResponse(profile=None, items=[])

    user_id = user_history[0].user_id
    recommendations = read_recommendations(cache, user_id)
    if recommendations is None:
        recommender = request.app.state.recommender
        if recommender is None:
            logger.warning("Recommendations for user %d asked for, but no food catalog is loaded.", user_id)
            raise HTTPException(status_code=503, detail="Recommendations are unavailable: the food catalog is not loaded.")
        started = time.perf_counter()
        recommendations = recommend(recommender, user_history)
        logger.info("Recommended %d foods for user %d from %d history recipes in %.2fs.",
                    len(recommendations.items), user_id, len(user_history), time.perf_counter() - started)
        write_recommendations(cache, user_id, recommendations)
    else:
        logger.debug("Recommendations for user %d served from the cache.", user_id)

    return recommendations.model_copy(update={"items": recommendations.items[:top_k]})

@router.get("/api/users/{username}", response_model=User, tags=["users"])
def get_user(username: str) -> User:
    """A user by username, for clients that know the username but need the user_id the other routes take."""
    connection = open_user_database()
    try:
        row = connection.execute(
            "SELECT user_id, username, json(preferences) AS preferences FROM app_user WHERE username = ?", (username,)
        ).fetchone()
    except sqlite3.Error:
        logger.exception("Reading user %r failed.", username)
        raise HTTPException(status_code=503, detail="User data is unavailable.")
    finally:
        connection.close()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return User(id=row["user_id"], username=row["username"],
                preferences=Preferences.model_validate_json(row["preferences"]))


@router.get("/api/users/{username}/likes", response_model=dict[str, bool], tags=["users"])
def get_user_likes(username: str, request: Request) -> dict[str, bool]:
    """Whether the user liked each recipe or catalog food they rated, by its id, so a client can show it."""
    connection = open_user_database()
    try:
        likes = fetch_user_likes(connection, request.app.state.cache, username)
    except sqlite3.Error:
        logger.exception("Reading the likes of %r failed.", username)
        raise HTTPException(status_code=503, detail="User data is unavailable.")
    finally:
        connection.close()
    if likes is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return likes


@router.put("/api/recipes/{recipe_id}/rating", response_model=UserHistory, tags=["recipe"])
def rate_recipe(
    recipe_id: str,
    user_id: int,
    like: bool,
    request: Request,
) -> UserHistory:
    """Record whether the user liked a recipe they cooked, or a catalog food by its fdc_id (a recommended grocery). Rating
    the same one again changes the like in their history rather than adding another. A recommendation they did not
    like also leaves their cached recommendations."""
    connection = open_user_database()
    try:
        if connection.execute("SELECT 1 FROM app_user WHERE user_id = ?", (user_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found.")
        # add_history checks the recipe or food exists, then updates its row of user_history or inserts one
        entry = add_history(connection, user_id, recipe_id, like)
    except LookupError:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    except sqlite3.Error:
        logger.exception("Recording whether user %d liked recipe %s failed.", user_id, recipe_id)
        raise HTTPException(status_code=503, detail="User data is unavailable.")
    finally:
        connection.close()
    if not like and drop_recommendation(request.app.state.cache, user_id, recipe_id):
        logger.debug("Dropped %s, which user %d did not like, from their cached recommendations.", recipe_id, user_id)
    return entry
