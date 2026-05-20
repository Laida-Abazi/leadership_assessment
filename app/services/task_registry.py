import json
import logging
from typing import Optional

from app.services.redis_client import redis_client


LOGGER = logging.getLogger(__name__)


class TaskRegistry:
    def register_signal_task(self, segment_id: str, candidate_id: str, task_id: str) -> None:
        signal_key = f"signal_task:{segment_id}"
        signal_list_key = f"signal_tasks_list:{candidate_id}"

        redis_client.set(signal_key, task_id, ex=3600)
        redis_client.rpush(signal_list_key, task_id)
        redis_client.expire(signal_list_key, 3600)

        LOGGER.debug("Registered signal task %s for segment %s", task_id, segment_id)

    def get_signal_task_ids(self, candidate_id: str) -> list[str]:
        signal_list_key = f"signal_tasks_list:{candidate_id}"
        task_ids = redis_client.lrange(signal_list_key, 0, -1)
        return list(task_ids) if task_ids else []

    def acquire_analysis_lock(self, candidate_id: str, task_id: str) -> bool:
        lock_key = f"analysis_lock:{candidate_id}"
        acquired = bool(redis_client.set(lock_key, task_id, nx=True, ex=3600))

        if acquired:
            LOGGER.info("Acquired analysis lock for candidate %s", candidate_id)
        else:
            LOGGER.info("Failed to acquire analysis lock for candidate %s", candidate_id)

        return acquired

    def release_analysis_lock(self, candidate_id: str) -> None:
        lock_key = f"analysis_lock:{candidate_id}"
        redis_client.delete(lock_key)
        LOGGER.debug("Released analysis lock for candidate %s", candidate_id)

    def is_analysis_running(self, candidate_id: str) -> bool:
        lock_key = f"analysis_lock:{candidate_id}"
        return bool(redis_client.exists(lock_key))

    def set_pipeline_id(self, candidate_id: str, pipeline_id: str) -> None:
        pipeline_key = f"pipeline:{candidate_id}"
        redis_client.set(pipeline_key, pipeline_id, ex=7200)

    def get_pipeline_id(self, candidate_id: str) -> Optional[str]:
        pipeline_key = f"pipeline:{candidate_id}"
        pipeline_id = redis_client.get(pipeline_key)

        if pipeline_id is None:
            return None

        return json.loads(json.dumps(pipeline_id))

    def clear_candidate_tasks(self, candidate_id: str) -> None:
        signal_list_key = f"signal_tasks_list:{candidate_id}"
        lock_key = f"analysis_lock:{candidate_id}"
        redis_client.delete(signal_list_key, lock_key)
        LOGGER.debug("Cleared task registry state for candidate %s", candidate_id)


task_registry = TaskRegistry()
