from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request
from sqlalchemy.exc import SQLAlchemyError

from .db import close_database, open_database, split_preview
from .clustering import cluster
from .cache import (
    RATINGS_BEFORE_REFRESH,
    count_rating,
    drop_recommendation,
    drop_recommendations,
    read_recommendations,
    reset_ratings,
    write_recommendations,
)
from .dependencies import Classifying, Clustering, Recipes, RecipeStore, Recommending, Users
from .inference.food_query import EmptyQuery, UnknownFood
from .recommend import random_recommendations, recommend
from .schema import has_food_data
from .repository.recipe_repository import is_food_id
from .schemas import (
    ClassifyRequest,
    ClusteringRequest,
    ClusteringResponse,
    FoodClassification,
    HealthResponse,
    HistoryItem,
    Preferences,
    Recipe,
    RecipeDetail,
    RecipeListResponse,
    RecipeSummary,
    RecommendationResponse,
    SavedItem,
    User,
    UserProfileInput,
    UserRating,
    UserRecipeCreate,
    UserRecipeResponse,
)
from .users import item_titles, rate_item, set_preference

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Report process health; recipe routes report data availability."""

    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse, tags=["health"])
def ready() -> HealthResponse:
    """Report readiness only after the database holds the normalized recipes the migration job loads."""

    connection = open_database()
    try:
        loaded = has_food_data(connection)
    except sqlite3.Error:
        logger.exception("Checking the recipes failed.")
        loaded = False
    finally:
        close_database(connection)
    if not loaded:
        logger.warning("The database has no recipes yet; the migration job loads them.")
        raise HTTPException(status_code=503, detail="Recipe data is unavailable.")
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
        # the recipe database's recipes and the ones users wrote; the catalog foods' rows are there for saved_recipe
        where = " WHERE r.kind IN ('openrecipes', 'user') AND r.title IS NOT NULL AND trim(r.title) <> ''"
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
            "WHERE recipe_id = ? AND kind IN ('openrecipes', 'user') AND title IS NOT NULL AND trim(title) <> ''",
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
        # recipe_tag is the API's (models.create_tables): a recipe database the migration job made alone has none
        has_tags = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'recipe_tag'"
        ).fetchone() is not None
        tags = [
            item[0]
            for item in connection.execute(
                "SELECT label FROM recipe_tag WHERE recipe_id = ? ORDER BY label", (recipe_id,)
            ).fetchall()
        ] if has_tags else []
        return RecipeDetail(
            id=row["recipe_id"],
            title=row["title"],
            ingredients=ingredients,
            instructions=instructions,
            quality_status=row["quality_status"],
            tags=tags,
        )
    except HTTPException:
        raise
    except sqlite3.Error:
        logger.exception("Reading recipe %s failed.", recipe_id)
        raise HTTPException(status_code=503, detail="Recipe data is unavailable.")
    finally:
        close_database(connection)

@router.post(
    "/api/recipes",
    response_model=UserRecipeResponse,
    status_code=201,
    tags=["recipes"],
    responses={404: {"description": "User not found."}, 503: {"description": "Recipe data is unavailable."}},
)
def create_recipe(body: UserRecipeCreate, users: Users, recipes: RecipeStore) -> UserRecipeResponse:
    """Save a recipe the user wrote themselves, with the tags they kept or added, and return it as stored. It also
    becomes one of the app's recipes, "user-recipe:<n>", which the user can save or rate."""
    try:
        if not users.user_exists(body.user_id):
            raise HTTPException(status_code=404, detail="User not found.")
        recipe = recipes.create_user_recipe(body)
        return UserRecipeResponse.model_validate(recipe)
    except SQLAlchemyError:
        logger.exception("Saving a recipe of user %d failed.", body.user_id)
        raise HTTPException(status_code=503, detail="Recipe data is unavailable.")


@router.get("/api/recipe/{fdc_id}", response_model=Recipe, tags=["recipes"])
def get_catalog_recipe(fdc_id: int, catalog: Recipes) -> Recipe:
    """One item of the priced catalog the recommendations come from: its ingredients with their FairPrice packs and
    prices, its nutrients per 100 g, and its tags (a Food.com recipe's)."""
    if catalog is None:
        raise HTTPException(status_code=503, detail="The recipe is not loaded.")
    try:
        item = catalog.food(fdc_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Catalog item not found.")
        return item.model_copy(update={"tags": catalog.tags(str(fdc_id))})
    except SQLAlchemyError:
        logger.exception("Reading catalog item %d failed.", fdc_id)
        raise HTTPException(status_code=503, detail="The recipe is unavailable.")


@router.get("/api/recommend/{username}", response_model=RecommendationResponse, tags=["recommendations"])
def get_recommendation(
    username: Annotated[str, Path()],
    request: Request,
    users: Users,
    catalog: Recipes,
    recommender: Recommending,
    top_k: Annotated[int, Query(ge=1, le=50)] = 10,
) -> RecommendationResponse:
    """The user's top_k recommendations. At cold start, until they have rated more than RATINGS_BEFORE_REFRESH items
    (the count their cached recommendations refresh at), random items their preferences allow, not cached, so the
    model takes over as soon as the history passes it; after that the model's, cached."""
    cache = request.app.state.cache
    user_history = users.fetch_user_history(username)
    if len(user_history) <= RATINGS_BEFORE_REFRESH:
        user = users.get_user(username)
        if user is None:
            logger.debug("No user %r, so no recommendations.", username)
            return RecommendationResponse(profile=None, items=[])
        if recommender is None or catalog is None:
            logger.warning("Recommendations for user %d asked for, but no recipe is loaded.", user.id)
            raise HTTPException(status_code=503, detail="Recommendations are unavailable: the recipe is not loaded.")
        profile = UserProfileInput(user_id=user.id, preferences=user.preferences, history=list(reversed(user_history)))
        recommendations = random_recommendations(recommender, profile, catalog)
        logger.debug("Cold start for user %d (%d of more than %d ratings): %d random foods.", user.id,
                     len(user_history), RATINGS_BEFORE_REFRESH, len(recommendations.items))
        return recommendations.model_copy(update={"items": recommendations.items[:top_k]})

    user_id = user_history[0].user_id
    recommendations = read_recommendations(cache, user_id)
    if recommendations is None:
        if recommender is None or catalog is None:
            logger.warning("Recommendations for user %d asked for, but no recipe is loaded.", user_id)
            raise HTTPException(status_code=503, detail="Recommendations are unavailable: the recipe is not loaded.")
        started = time.perf_counter()
        user = users.get_user(username)
        # fetch_user_history reads newest first; the History is oldest first
        profile = UserProfileInput(user_id=user_id, preferences=user.preferences if user else Preferences(),
                                   history=list(reversed(user_history)))
        recommendations = recommend(recommender, profile, catalog)
        logger.info("Recommended %d foods for user %d from %d history recipes in %.2fs.",
                    len(recommendations.items), user_id, len(user_history), time.perf_counter() - started)
        write_recommendations(cache, user_id, recommendations)
    else:
        logger.debug("Recommendations for user %d served from the cache.", user_id)

    return recommendations.model_copy(update={"items": recommendations.items[:top_k]})

def food_classification(result: dict) -> FoodClassification:
    """RecipeClassifier.predict's result → the response, with the training labels of a looked-up food."""
    item = result["item"]
    return FoodClassification(
        fdc_id=item.get("fdc_id"), description=item["description"],
        product=item["product"], adj=item["adj"], verb=item["verb"], nutrients=item["nutrients"],
        terms=result["terms"], known_terms=result["known_terms"], model=result["model"], labels=result["labels"],
        score_kind=result["score_kind"], scores=result["scores"],
        training_labels=item.get("training", {}).get("labels"),
    )


def classify_or_raise(classify, *args) -> FoodClassification:
    started = time.perf_counter()
    try:
        result = classify(*args)
    except UnknownFood as error:
        raise HTTPException(status_code=404, detail=str(error))
    except EmptyQuery as error:
        raise HTTPException(status_code=422, detail=str(error))
    except FileNotFoundError:
        logger.exception("The fdc_id lookup is missing.")
        raise HTTPException(status_code=503, detail="Food lookup is unavailable.")
    logger.info("Classified %r as %s in %.2fs.", result["item"]["description"], result["labels"],
                time.perf_counter() - started)
    return food_classification(result)


@router.post("/api/classify", response_model=FoodClassification, tags=["classification"])
def classify_food(body: ClassifyRequest, classifier: Classifying) -> FoodClassification:
    """Tag a food with recipe classes (the gated classifier chain of recipe_classification.ipynb). The query is a
    food description, optionally with nutrients per 100 g written into it; product / adj / verb terms are extracted
    from it unless given."""
    terms = {"product": body.product, "adj": body.adj, "verb": body.verb}
    return classify_or_raise(classifier.classify, body.query, body.nutrients, terms)


@router.get("/api/classify/{fdc_id}", response_model=FoodClassification, tags=["classification"])
def classify_training_food(
    fdc_id: Annotated[int, Path(openapi_examples={"waffles": {"summary": "Waffles, buttermilk", "value": 167516}})],
    classifier: Classifying,
) -> FoodClassification:
    """Tag a training food, looked up by fdc_id, with recipe classes; `training_labels` holds its labels in the
    training data for comparison."""
    return classify_or_raise(classifier.classify_fdc, fdc_id)


@router.post("/api/cluster", response_model=ClusteringResponse, tags=["clustering"])
def cluster_recipe(
    body: ClusteringRequest,
    clusterer: Clustering,
    catalog: Recipes,
    k: Annotated[int, Query(ge=1, le=50)] = 5,
) -> ClusteringResponse:
    """The k catalog foods nearest the item in its recipe cluster (the HDBSCAN model of recipe_clustering.ipynb). The
    model reads the item's title as a food description; its models are loaded at startup."""
    if catalog is None:
        raise HTTPException(status_code=503, detail="Clustering is unavailable: no recipe is loaded.")
    started = time.perf_counter()
    try:
        response = cluster(clusterer, body, k, catalog)
    except EmptyQuery as error:
        raise HTTPException(status_code=422, detail=str(error))
    logger.info("Clustered %r in %.2fs.", body.item.title, time.perf_counter() - started)
    return response


@router.post("/api/guests", response_model=User, status_code=201, tags=["users"])
def create_guest(users: Users) -> User:
    """A new guest user, for one browser. The app keeps its username in a cookie and sends it with each request."""
    try:
        return users.create_user(f"guest-{uuid.uuid4().hex}")
    except SQLAlchemyError:
        logger.exception("Creating a guest user failed.")
        raise HTTPException(status_code=503, detail="User data is unavailable.")


@router.get("/api/users/{username}", response_model=User, tags=["users"])
def get_user(username: str, users: Users) -> User:
    """A user by username, for clients that know the username but need the user_id the other routes take."""
    try:
        user = users.get_user(username)
    except SQLAlchemyError:
        logger.exception("Reading user %r failed.", username)
        raise HTTPException(status_code=503, detail="User data is unavailable.")
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


@router.get(
    "/api/users/{username}/recipes",
    response_model=list[UserRecipeResponse],
    tags=["users"],
    responses={404: {"description": "User not found."}, 503: {"description": "Recipe data is unavailable."}},
)
def get_user_recipes(username: str, users: Users, recipes: RecipeStore) -> list[UserRecipeResponse]:
    """The recipes the user wrote themselves, newest first, each whole: its title, ingredient and instruction lines,
    tags, nutrients per 100 g and when it was added, read in one query joining the recipe tables."""
    try:
        user_id = users.find_user_id(username)
        if user_id is None:
            raise HTTPException(status_code=404, detail="User not found.")
        return [UserRecipeResponse.model_validate(recipe) for recipe in recipes.user_recipes(user_id)]
    except SQLAlchemyError:
        logger.exception("Reading the recipes of %r failed.", username)
        raise HTTPException(status_code=503, detail="Recipe data is unavailable.")


@router.get("/api/users/{username}/likes", response_model=dict[str, bool], tags=["users"])
def get_user_likes(username: str, users: Users) -> dict[str, bool]:
    """Whether the user liked each recipe or catalog food they rated, by its id, so a client can show it."""
    try:
        likes = users.fetch_user_likes(username)
    except SQLAlchemyError:
        logger.exception("Reading the likes of %r failed.", username)
        raise HTTPException(status_code=503, detail="User data is unavailable.")
    if likes is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return likes


@router.put("/api/users/{username}/set-preferences", response_model=User, tags=["users"])
def set_user_preferences(username: str, preferences: Preferences, request: Request, users: Users) -> User:
    """Replace the user's preferences with the body's. Their cached recommendations were made for the old ones, so
    they are dropped and the next request recommends again."""
    try:
        user_id = users.find_user_id(username)
        user = None if user_id is None else set_preference(users, user_id, preferences)
    except SQLAlchemyError:
        logger.exception("Setting the preferences of %r failed.", username)
        raise HTTPException(status_code=503, detail="User data is unavailable.")
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    # cache is now stale
    if drop_recommendations(request.app.state.cache, user.id):
        logger.debug("Dropped the cached recommendations of user %d, whose preferences changed.", user.id)
    return user


@router.get("/api/users/{username}/history", response_model=list[HistoryItem], tags=["users"])
def get_user_history(
    username: str,
    users: Users,
    catalog: Recipes,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[HistoryItem]:
    """The user's history, newest first: each recipe or recommended food they rated, its title, and whether they liked
    it."""
    try:
        if users.find_user_id(username) is None:
            raise HTTPException(status_code=404, detail="User not found.")
        rows = users.fetch_user_history(username, limit)
    except SQLAlchemyError:
        logger.exception("Reading the history of %r failed.", username)
        raise HTTPException(status_code=503, detail="User data is unavailable.")
    
    titles = item_titles((row.recipe_id for row in rows), catalog)
    return [
        HistoryItem(recipe_id=row.recipe_id, kind="food" if is_food_id(row.recipe_id) else "recipe",
                    title=titles.get(row.recipe_id), like=row.like, timestamp=row.timestamp)
        for row in rows
    ]


@router.get("/api/users/{username}/saved", response_model=list[SavedItem], tags=["users"])
def get_saved_recipes(username: str, users: Users) -> list[SavedItem]:
    """The recipes the user saved for later, newest first: Food.com recipes, catalog foods and their own."""
    try:
        saved = users.fetch_saved(username)
    except SQLAlchemyError:
        logger.exception("Reading the saved recipes of %r failed.", username)
        raise HTTPException(status_code=503, detail="User data is unavailable.")
    if saved is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return saved


@router.put("/api/users/{username}/saved/{recipe_id}", response_model=SavedItem, tags=["users"])
def save_recipe(username: str, recipe_id: str, users: Users) -> SavedItem:
    """Save a recipe for later: a Food.com recipe, a catalog food by its fdc_id, or a user's own. Saving it again keeps
    it as it was."""
    try:
        user_id = users.find_user_id(username)
        if user_id is None:
            raise HTTPException(status_code=404, detail="User not found.")
        saved = users.save_recipe(user_id, recipe_id)
    except SQLAlchemyError:
        logger.exception("Saving recipe %s for %r failed.", recipe_id, username)
        raise HTTPException(status_code=503, detail="User data is unavailable.")
    if saved is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    return saved


@router.delete("/api/users/{username}/saved/{recipe_id}", status_code=204, tags=["users"])
def unsave_recipe(username: str, recipe_id: str, users: Users) -> None:
    """Take a recipe out of the user's saved ones; one they had not saved is gone already."""
    try:
        user_id = users.find_user_id(username)
        if user_id is None:
            raise HTTPException(status_code=404, detail="User not found.")
        users.unsave_recipe(user_id, recipe_id)
    except SQLAlchemyError:
        logger.exception("Unsaving recipe %s for %r failed.", recipe_id, username)
        raise HTTPException(status_code=503, detail="User data is unavailable.")


@router.put("/api/recipes/{recipe_id}/rating", response_model=UserRating, tags=["recipe"])
def rate_recipe(
    recipe_id: str,
    user_id: int,
    like: bool,
    request: Request,
    users: Users,
    catalog: Recipes,
) -> UserRating:
    """Record whether the user liked a recipe they cooked, or a catalog food by its fdc_id (a recommended grocery). Rating
    the same one again changes the like in their history rather than adding another. Each rating is counted in Redis;
    once the user has rated more than RATINGS_BEFORE_REFRESH since their recommendations were cached, those are
    dropped, so the next request recommends again from the history the ratings changed, and the count starts over."""
    try:
        if not users.user_exists(user_id):
            raise HTTPException(status_code=404, detail="User not found.")
        # rate_item checks the recipe or food exists, then updates its row of user_history or inserts one
        entry = rate_item(users, catalog, user_id, recipe_id, like)
    except LookupError:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    except SQLAlchemyError:
        logger.exception("Recording whether user %d liked recipe %s failed.", user_id, recipe_id)
        raise HTTPException(status_code=503, detail="User data is unavailable.")
    cache = request.app.state.cache
    ratings = count_rating(cache, user_id)
    if ratings is not None and ratings > RATINGS_BEFORE_REFRESH:
        if drop_recommendations(cache, user_id):
            logger.debug("Refreshed recommendation for %d", user_id)
        reset_ratings(cache, user_id)
    # if not like and drop_recommendation(request.app.state.cache, user_id, recipe_id):
    #     logger.debug("Dropped %s, which user %d did not like, from their cached recommendations.", recipe_id, user_id)
    return entry
