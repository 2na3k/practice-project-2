from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from datetime import datetime, timezone

from .recipe import DigestedRecipe

# As many rated recipes as users.fetch_user_history reads for a recommendation.
DEFAULT_MAX_ENTRIES = 50

from .schemas import UserRating

class History:
    """A user's rated recipes as a queue, oldest first. add puts a rating at the back; once the queue holds
    max_entries, each new one pushes the oldest off the front."""

    def __init__(self, user_id: int, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.user_id = user_id
        self.history: deque[UserRating] = deque(maxlen=max_entries)

    @classmethod
    def from_ratings(cls, user_id: int, ratings: list[UserRating], max_entries: int = DEFAULT_MAX_ENTRIES) -> History:
        """The history of `ratings`, given oldest first; past max_entries, only the newest are kept, as with add."""
        history = cls(user_id, max_entries)
        for rating in ratings:
            history.add(rating)
        return history

    def add(self, entry: UserRating) -> None:
        self.history.append(entry)

    def __len__(self) -> int:
        return len(self.history)

    def __iter__(self) -> Iterator[UserRating]:
        return iter(self.history)
