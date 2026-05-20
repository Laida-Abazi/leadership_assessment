import logging
import os
import time

from app.services.redis_client import redis_client


LOGGER = logging.getLogger(__name__)


class GeminiRateLimiter:
    def __init__(self) -> None:
        self.rpm_limit = int(os.getenv("GEMINI_RPM_LIMIT", "60"))
        self.window_seconds = int(os.getenv("GEMINI_WINDOW_SECONDS", "60"))

    def check_and_increment(self) -> bool:
        bucket = int(time.time() // self.window_seconds)
        key = f"ratelimit:gemini:{bucket}"

        try:
            current_count = redis_client.incr(key)
            if current_count == 1:
                redis_client.expire(key, self.window_seconds * 2)

            if current_count <= self.rpm_limit:
                return True

            LOGGER.warning(
                "Gemini rate limit hit for bucket %s: %s/%s",
                bucket,
                current_count,
                self.rpm_limit,
            )
            return False
        except Exception as exc:
            LOGGER.warning("Redis unavailable during Gemini rate limit check: %s", exc)
            return True

    def wait_for_slot(self, max_wait_seconds: int = 65) -> bool:
        deadline = time.time() + max_wait_seconds

        while time.time() <= deadline:
            if self.check_and_increment():
                return True
            time.sleep(1)

        return False

    def get_current_usage(self) -> dict:
        bucket = int(time.time() // self.window_seconds)
        key = f"ratelimit:gemini:{bucket}"

        try:
            current_value = redis_client.get(key)
            current_count = int(current_value) if current_value is not None else 0
        except Exception as exc:
            LOGGER.warning("Redis unavailable while reading Gemini rate limit usage: %s", exc)
            current_count = 0

        remaining = max(self.rpm_limit - current_count, 0)
        return {
            "current_count": current_count,
            "limit": self.rpm_limit,
            "window_seconds": self.window_seconds,
            "remaining": remaining,
        }


gemini_rate_limiter = GeminiRateLimiter()
