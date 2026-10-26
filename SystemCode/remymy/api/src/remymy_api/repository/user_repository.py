"""The users, their history and their saved recipes, through the ORM (models.AppUser, models.UserHistoryEntry and
models.SavedRecipe)."""
from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager

from redis import Redis
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..cache import read_user_id, write_user_id
from ..models import NOW, AppUser, RecipeEntry, SavedRecipe, UserHistoryEntry
from ..schemas import Preferences, SavedItem, User, UserRating

logger = logging.getLogger(__name__)

# recipe.kind as a SavedItem's kind
SAVED_KINDS = {"openrecipes": "recipe", "catalog": "food", "user": "user"}


class UserRepository:
    """The app's users, looked up by username through the Redis cache first, and the likes in their history.

    One is made per request on that request's session (dependencies.get_user_repository). Each write commits its own
    transaction, as the SQL version did; a failed one is rolled back, so the repository can be used again."""

    def __init__(self, session: Session, cache: Redis) -> None:
        self.session = session
        self.cache = cache

    @contextmanager
    def _transaction(self) -> Generator[None, None, None]:
        """Commit what the block writes, or roll it back if it raises. (Not session.begin(): a lookup earlier in the
        request has begun a transaction already.)"""
        try:
            yield
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def find_user_id(self, username: str) -> int | None:
        user_id = read_user_id(self.cache, username)
        if user_id is not None:
            return user_id
        user_id = self.session.scalar(select(AppUser.user_id).where(AppUser.username == username))
        if user_id is None:
            return None
        write_user_id(self.cache, username, user_id)
        return user_id

    def username_exists(self, username: str) -> bool:
        return self.find_user_id(username) is not None

    def user_exists(self, user_id: int) -> bool:
        return self.session.scalar(select(AppUser.user_id).where(AppUser.user_id == user_id)) is not None

    def get_user(self, username: str) -> User | None:
        user = self.session.scalar(select(AppUser).where(AppUser.username == username))
        if user is None:
            return None
        return User(id=user.user_id, username=user.username, preferences=Preferences.model_validate(user.preferences))

    def create_user(self, username: str, preferences: Preferences | None = None) -> User:
        preferences = preferences or Preferences()
        user = AppUser(username=username, preferences=preferences.model_dump())
        with self._transaction():
            self.session.add(user)
            self.session.flush()
            user_id = user.user_id
        write_user_id(self.cache, username, user_id)
        logger.info("Created user %r with id %d.", username, user_id)
        return User(id=user_id, username=username, preferences=preferences)

    def fetch_user_history(self, username: str, limit: int = 50) -> list[UserRating]:
        """The user's likes, newest first; none for an unknown username."""
        user_id = self.find_user_id(username)
        if user_id is None:
            return []
        entries = self.session.scalars(
            select(UserHistoryEntry)
            .where(UserHistoryEntry.user_id == user_id)
            .order_by(UserHistoryEntry.timestamp.desc(), UserHistoryEntry.history_id.desc())
            .limit(limit)
        )
        return [UserRating.model_validate(entry) for entry in entries]

    def fetch_user_likes(self, username: str) -> dict[str, bool] | None:
        """Whether the user liked each recipe or catalog food they rated, by its id, as their latest row for it says;
        None for an unknown username."""
        user_id = self.find_user_id(username)
        if user_id is None:
            return None
        rows = self.session.execute(
            select(UserHistoryEntry.recipe_id, UserHistoryEntry.like)
            .where(UserHistoryEntry.user_id == user_id)
            # oldest first, so a later row of the same recipe overwrites an earlier one
            .order_by(UserHistoryEntry.timestamp, UserHistoryEntry.history_id)
        )
        return {recipe_id: like for recipe_id, like in rows}

    def add_history(self, user_id: int, recipe_id: str, like: bool) -> UserRating:
        """Record whether the user liked a recipe, or a catalog food by its fdc_id. One they rated before has its like
        changed and dated now; anything else gets a new row. Callers check the recipe or food exists (users.rate_item);
        an unknown user_id is an IntegrityError."""
        with self._transaction():
            timestamps = self.session.scalars(
                update(UserHistoryEntry)
                .where(UserHistoryEntry.user_id == user_id, UserHistoryEntry.recipe_id == recipe_id)
                .values(like=like, timestamp=NOW)
                .returning(UserHistoryEntry.timestamp)
            ).all()
            if timestamps:
                timestamp = timestamps[0]
            else:
                entry = UserHistoryEntry(user_id=user_id, recipe_id=recipe_id, like=like)
                self.session.add(entry)
                self.session.flush()
                timestamp = entry.timestamp
        logger.debug("User %d %s recipe %s.", user_id, "liked" if like else "did not like", recipe_id)
        return UserRating(user_id=user_id, timestamp=timestamp, like=like, recipe_id=recipe_id)

    def set_user_preferences(self, user_id: int, preferences: Preferences) -> User | None:
        """Replace the user's preferences with these; None for an unknown user_id."""
        with self._transaction():
            user = self.session.get(AppUser, user_id)
            if user is None:
                return None
            user.preferences = preferences.model_dump()
        logger.debug("Set the preferences of user %d.", user_id)
        return User(id=user.user_id, username=user.username, preferences=preferences)

    def fetch_saved(self, username: str) -> list[SavedItem] | None:
        """The recipes the user saved, newest first; None for an unknown username."""
        user_id = self.find_user_id(username)
        if user_id is None:
            return None
        rows = self.session.execute(
            select(SavedRecipe.recipe_id, SavedRecipe.saved_at, RecipeEntry.title, RecipeEntry.kind)
            .join(RecipeEntry, RecipeEntry.recipe_id == SavedRecipe.recipe_id)
            .where(SavedRecipe.user_id == user_id)
            .order_by(SavedRecipe.saved_at.desc(), SavedRecipe.recipe_id)
        )
        return [SavedItem(recipe_id=recipe_id, kind=SAVED_KINDS[kind], title=title, saved_at=saved_at)
                for recipe_id, saved_at, title, kind in rows]

    def save_recipe(self, user_id: int, recipe_id: str) -> SavedItem | None:
        """Save a recipe of any kind for the user; one saved already keeps the time it was. None for a recipe the
        database does not know. Callers check the user exists; an unknown user_id is an IntegrityError."""
        with self._transaction():
            recipe = self.session.get(RecipeEntry, recipe_id)
            if recipe is None:
                return None
            saved = self.session.get(SavedRecipe, (user_id, recipe_id))
            if saved is None:
                saved = SavedRecipe(user_id=user_id, recipe_id=recipe_id)
                self.session.add(saved)
                self.session.flush()
            item = SavedItem(recipe_id=recipe_id, kind=SAVED_KINDS[recipe.kind], title=recipe.title,
                             saved_at=saved.saved_at)
        logger.debug("User %d saved recipe %s.", user_id, recipe_id)
        return item

    def unsave_recipe(self, user_id: int, recipe_id: str) -> bool:
        """Take a recipe out of the user's saved ones; whether it was saved."""
        with self._transaction():
            removed = self.session.execute(
                delete(SavedRecipe).where(SavedRecipe.user_id == user_id, SavedRecipe.recipe_id == recipe_id)
            ).rowcount
        return bool(removed)
