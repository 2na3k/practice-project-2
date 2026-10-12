from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from datetime import datetime, timezone

from .recipe import Recipe

# As many rated recipes as users.fetch_user_history reads for a recommendation.
DEFAULT_MAX_ENTRIES = 50


class RecipeHistory:
    """One rating of a recipe. It refers to the Recipe instead of copying it, so a recipe rated twice is one object."""

    def __init__(self, recipe: Recipe, rating: int | None = None, timestamp: datetime | None = None) -> None:
        # 0 to 5 as in user_history; None is a recipe cooked but not rated
        if rating is not None and not 0 <= rating <= 5:
            raise ValueError(f"rating must be 0 to 5 or None, not {rating!r}")
        self.recipe = recipe
        self.rating = rating
        # when it was rated: the recommender orders the history by it and cuts it into windows at long gaps
        self.timestamp = timestamp or datetime.now(timezone.utc)


class History:
    """A user's rated recipes as a queue, oldest first. add puts a rating at the back; once the queue holds
    max_entries, each new one pushes the oldest off the front."""

    def __init__(self, user_id: int, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.user_id = user_id
        self.history: deque[RecipeHistory] = deque(maxlen=max_entries)

    def add(self, entry: RecipeHistory) -> None:
        self.history.append(entry)

    def __len__(self) -> int:
        return len(self.history)

    def __iter__(self) -> Iterator[RecipeHistory]:
        return iter(self.history)
