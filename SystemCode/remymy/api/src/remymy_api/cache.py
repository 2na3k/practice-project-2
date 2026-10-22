from __future__ import annotations

import logging
import os

import redis
from pydantic import ValidationError

from .schemas import RecommendationResponse

logger = logging.getLogger(__name__)

DEFAULT_REDIS_URL = "redis://redis:6379/0"
DEFAULT_TTL_SECONDS = 86400
# A user's cached recommendations are made again once they have rated more than this many items since they were
# cached; the count lives a day from the user's latest rating.
RATINGS_BEFORE_REFRESH = 10
RATINGS_TTL_SECONDS = 86400


def connect_cache() -> redis.Redis:
    return redis.Redis.from_url(
        os.getenv("REDIS_URL", DEFAULT_REDIS_URL),
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def cache_ttl() -> int:
    return int(os.getenv("CACHE_TTL_SECONDS", DEFAULT_TTL_SECONDS))


def user_key(username: str) -> str:
    return f"user:{username}"


# Redis is only the cache: every failure below is logged and the caller carries on without it.


def read_user_id(cache: redis.Redis, username: str) -> int | None:
    try:
        raw = cache.get(user_key(username))
        return None if raw is None else int(raw)
    except redis.RedisError as error:
        logger.warning("Redis read of %s failed: %s", user_key(username), error)
        return None
    except ValueError:
        logger.warning("Redis holds a non-integer user id at %s; ignoring it.", user_key(username))
        return None


def write_user_id(cache: redis.Redis, username: str, user_id: int) -> None:
    try:
        cache.set(user_key(username), user_id, ex=cache_ttl())
    except redis.RedisError as error:
        logger.warning("Redis write of %s failed: %s", user_key(username), error)


def recommendations_key(user_id: int) -> str:
    return f"recommendations:{user_id}"


def read_recommendations(cache: redis.Redis, user_id: int) -> RecommendationResponse | None:
    try:
        raw = cache.get(recommendations_key(user_id))
        return None if raw is None else RecommendationResponse.model_validate_json(raw)
    except redis.RedisError as error:
        logger.warning("Redis read of %s failed: %s", recommendations_key(user_id), error)
        return None
    except ValidationError:
        logger.warning("Cached %s does not match the response schema; recomputing it.", recommendations_key(user_id))
        return None


def write_recommendations(
    cache: redis.Redis, user_id: int, recommendations: RecommendationResponse
) -> bool:
    try:
        cache.set(recommendations_key(user_id), recommendations.model_dump_json(), ex=cache_ttl())
    except redis.RedisError as error:
        logger.warning("Redis write of %s failed: %s", recommendations_key(user_id), error)
        return False
    return True


def ratings_key(user_id: int) -> str:
    return f"number_of_ratings:{user_id}"


def count_rating(cache: redis.Redis, user_id: int) -> int | None:
    """Count one more rating by the user, and keep the count a day from now; the count so far, None without Redis."""
    key = ratings_key(user_id)
    try:
        count = cache.incr(key)
        cache.expire(key, RATINGS_TTL_SECONDS)
    except redis.RedisError as error:
        logger.warning("Redis count of %s failed: %s", key, error)
        return None
    return int(count)


def reset_ratings(cache: redis.Redis, user_id: int) -> None:
    """Forget the user's count of ratings, so their next rating counts from 1."""
    try:
        cache.delete(ratings_key(user_id))
    except redis.RedisError as error:
        logger.warning("Redis delete of %s failed: %s", ratings_key(user_id), error)


def drop_recommendations(cache: redis.Redis, user_id: int) -> bool:
    """Forget the user's cached recommendations, so the next request computes them again. True when there were some."""
    try:
        return bool(cache.delete(recommendations_key(user_id)))
    except redis.RedisError as error:
        logger.warning("Redis delete of %s failed: %s", recommendations_key(user_id), error)
        return False


def drop_recommendation(cache: redis.Redis, user_id: int, item_id: str) -> bool:
    """Take one item out of the user's cached recommendations, so the next request goes on without it; the entry keeps
    the time it had left. True when the item was cached and is gone."""
    key = recommendations_key(user_id)
    recommendations = read_recommendations(cache, user_id)
    if recommendations is None:
        return False
    items = [item for item in recommendations.items if item.id != item_id]
    if len(items) == len(recommendations.items):
        return False
    try:
        # TTL rather than SET ... KEEPTTL, which needs Redis 6
        remaining = cache.ttl(key)
        cache.set(key, recommendations.model_copy(update={"items": items}).model_dump_json(),
                  ex=remaining if remaining and remaining > 0 else cache_ttl())
    except redis.RedisError as error:
        logger.warning("Redis write of %s failed: %s", key, error)
        return False
    return True

