"""Shared Redis client. Import redis_client for all application use. Do NOT create new Redis connections elsewhere."""

import os

import redis


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
redis_client_bytes = redis.Redis.from_url(REDIS_URL)


def get_redis():
    return redis_client


def ping_redis() -> bool:
    try:
        redis_client.ping()
        return True
    except Exception:
        return False
