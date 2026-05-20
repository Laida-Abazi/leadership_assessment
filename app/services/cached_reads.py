import asyncio
import json
import logging
from typing import Optional, TYPE_CHECKING

from app.services.redis_client import redis_client


# These will be wired in Task 3.1.
if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


LOGGER = logging.getLogger(__name__)

ASSESSMENT_CACHE_TTL = 86400
CANDIDATE_CONTEXT_CACHE_TTL = 1800
JOB_REQUIREMENT_PROFILE_CACHE_TTL = 21600


async def _get_cached_json(key: str) -> Optional[object]:
    try:
        value = await asyncio.to_thread(redis_client.get, key)
        if value is None:
            return None
        return json.loads(value)
    except Exception as exc:
        LOGGER.warning("Redis unavailable while reading cache key %s: %s", key, exc)
        return None


async def _set_cached_json(key: str, value: object, ttl: int) -> None:
    try:
        payload = json.dumps(value)
        await asyncio.to_thread(redis_client.set, key, payload, ex=ttl)
    except Exception as exc:
        LOGGER.warning("Redis unavailable while writing cache key %s: %s", key, exc)


async def _delete_cache_keys(*keys: str) -> None:
    try:
        await asyncio.to_thread(redis_client.delete, *keys)
    except Exception as exc:
        LOGGER.warning("Redis unavailable while deleting cache keys %s: %s", keys, exc)


async def get_assessment_definition_cached(assessment_id: str, fetch_fn) -> dict:
    key = f"cache:assessment:{assessment_id}:definition"
    cached_value = await _get_cached_json(key)
    if cached_value is not None:
        LOGGER.debug("Cache hit for %s", key)
        return cached_value

    LOGGER.debug("Cache miss for %s", key)
    result = await fetch_fn(assessment_id)
    await _set_cached_json(key, result, ASSESSMENT_CACHE_TTL)
    return result


async def get_assessment_items_cached(assessment_id: str, fetch_fn) -> list:
    key = f"cache:assessment:{assessment_id}:items"
    cached_value = await _get_cached_json(key)
    if cached_value is not None:
        LOGGER.debug("Cache hit for %s", key)
        return cached_value

    LOGGER.debug("Cache miss for %s", key)
    result = await fetch_fn(assessment_id)
    await _set_cached_json(key, result, ASSESSMENT_CACHE_TTL)
    return result


async def get_candidate_context_cached(candidate_id: str, fetch_fn) -> dict:
    key = f"cache:candidate:{candidate_id}:context"
    cached_value = await _get_cached_json(key)
    if cached_value is not None:
        LOGGER.debug("Cache hit for %s", key)
        return cached_value

    LOGGER.debug("Cache miss for %s", key)
    result = await fetch_fn(candidate_id)
    await _set_cached_json(key, result, CANDIDATE_CONTEXT_CACHE_TTL)
    return result


async def get_job_requirement_profile_cached(job_req_id: str, fetch_fn) -> dict:
    key = f"cache:job_req_profile:{job_req_id}"
    cached_value = await _get_cached_json(key)
    if cached_value is not None:
        LOGGER.debug("Cache hit for %s", key)
        return cached_value

    LOGGER.debug("Cache miss for %s", key)
    result = await fetch_fn(job_req_id)
    await _set_cached_json(key, result, JOB_REQUIREMENT_PROFILE_CACHE_TTL)
    return result


async def invalidate_assessment_cache(assessment_id: str) -> None:
    definition_key = f"cache:assessment:{assessment_id}:definition"
    items_key = f"cache:assessment:{assessment_id}:items"
    await _delete_cache_keys(definition_key, items_key)
    LOGGER.info("Invalidated assessment cache for assessment %s", assessment_id)


async def invalidate_candidate_context(candidate_id: str) -> None:
    key = f"cache:candidate:{candidate_id}:context"
    await _delete_cache_keys(key)


async def invalidate_job_requirement_profile(job_req_id: str) -> None:
    key = f"cache:job_req_profile:{job_req_id}"
    await _delete_cache_keys(key)
