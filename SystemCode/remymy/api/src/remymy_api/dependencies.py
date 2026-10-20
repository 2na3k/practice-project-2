"""FastAPI dependencies that give a route its repositories, each on a session of its database for the request.
Tests swap one with app.dependency_overrides."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .db import get_catalog_db, get_db
from .repository.recipe_repository import RecipeRepository
from .repository.user_repository import UserRepository


def get_user_repository(request: Request, session: Annotated[Session, Depends(get_db)]) -> UserRepository:
    return UserRepository(session, request.app.state.cache)


def get_recipe_repository(session: Annotated[Session | None, Depends(get_catalog_db)]) -> RecipeRepository | None:
    """The catalog's repository, or None when there is no catalog database: the routes that need one answer 503."""
    return None if session is None else RecipeRepository(session)


Users = Annotated[UserRepository, Depends(get_user_repository)]
Catalog = Annotated[RecipeRepository | None, Depends(get_recipe_repository)]
