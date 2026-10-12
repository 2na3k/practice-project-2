from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from datetime import datetime, timezone

from .recipe import Recipe

# As many rated recipes as users.fetch_user_history reads for a recommendation.
DEFAULT_MAX_ENTRIES = 50

from .schemas import UserHistory

class History:
    """A user's rated recipes as a queue, oldest first. add puts a rating at the back; once the queue holds
    max_entries, each new one pushes the oldest off the front."""

    def __init__(self, user_id: int, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.user_id = user_id
        self.history: deque[UserHistory] = deque(maxlen=max_entries)

    def add(self, entry: UserHistory) -> None:
        self.history.append(entry)

    def __len__(self) -> int:
        return len(self.history)

    def __iter__(self) -> Iterator[UserHistory]:
        return iter(self.history)
