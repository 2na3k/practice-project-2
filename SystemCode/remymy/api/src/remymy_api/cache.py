from __future__ import annotations

import logging
import os

import redis
from pydantic import ValidationError

from .schemas import RecommendationResponse

logger = logging.getLogger(__name__)

DEFAULT_REDIS_URL = "redis://redis:6379/0"
DEFAULT_TTL_SECONDS = 86400


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
