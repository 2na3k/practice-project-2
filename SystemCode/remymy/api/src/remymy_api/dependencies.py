"""FastAPI dependencies that give a route its repositories, each on a session of its database for the request, and
the inference services startup loaded. Tests swap one with app.dependency_overrides."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_catalog_db, get_db
from .inference.classification_service import FoodClassifier
from .inference.service import Clusterer, Recommender
from .repository.recipe_repository import RecipeRepository
from .repository.user_repository import UserRepository


def get_user_repository(request: Request, session: Annotated[Session, Depends(get_db)]) -> UserRepository:
    return UserRepository(session, request.app.state.cache)


def get_recipe_repository(session: Annotated[Session | None, Depends(get_catalog_db)]) -> RecipeRepository | None:
    """The catalog's repository, or None when there is no catalog database: the routes that need one answer 503."""
    return None if session is None else RecipeRepository(session)


def get_recommender() -> Recommender | None:
    """The recommender startup loaded, or None when there was no catalog: the route answers 503 when it needs one."""
    return Recommender() if Recommender.loaded() else None


def get_classifier() -> FoodClassifier:
    """The classifier startup loaded; 503 when it did not (no weights, packages or models)."""
    if not FoodClassifier.loaded():
        raise HTTPException(status_code=503, detail="Classification is unavailable: the classifier is not loaded.")
    return FoodClassifier()


def get_clusterer() -> Clusterer:
    """The clusterer startup loaded; 503 when it did not (no weights, packages or models)."""
    if not Clusterer.loaded():
        raise HTTPException(status_code=503, detail="Clustering is unavailable: the clusterer is not loaded.")
    return Clusterer()


Users = Annotated[UserRepository, Depends(get_user_repository)]
Catalog = Annotated[RecipeRepository | None, Depends(get_recipe_repository)]
Recommending = Annotated[Recommender | None, Depends(get_recommender)]
Classifying = Annotated[FoodClassifier, Depends(get_classifier)]
Clustering = Annotated[Clusterer, Depends(get_clusterer)]
