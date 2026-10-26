"""One instance per process for the inference services, so a route constructs the service its startup loaded."""
from __future__ import annotations

import threading


class Singleton:
    """Every construction of a subclass returns its one instance. The first runs the subclass's _load with the
    arguments it was given, under a lock so two threads constructing it at once do not both load; later constructions
    ignore their arguments."""

    _instance: Singleton | None = None
    _initialized = False

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        # each subclass its own instance and lock
        cls._instance = None
        cls._creation_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._creation_lock:
                # Double-checked locking pattern for thread safety
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, *args, **kwargs) -> None:
        with self._creation_lock:
            if self._initialized:
                return
            self._load(*args, **kwargs)
            # only once _load returned: after a failed load the next construction loads again
            self._initialized = True

    def _load(self, *args, **kwargs) -> None:
        raise NotImplementedError

    @classmethod
    def loaded(cls) -> bool:
        """Whether the instance is constructed, so constructing it again returns it without loading anything."""
        return cls._instance is not None and cls._instance._initialized
