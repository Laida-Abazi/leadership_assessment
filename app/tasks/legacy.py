import asyncio
import logging

from celery.exceptions import Retry as CeleryRetry

try:
    from app.celery.celery_app import celery_app
except ImportError:  # pragma: no cover - compatibility with current package layout
    from app.celery.celery_app import celery_app

from app.services.task_registry import task_registry
from app.tasks.db import get_sync_db


LOGGER = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.legacy_run_analysis",
    bind=True,
    max_retries=2,
    default_retry_delay=20,
    acks_late=True,
    queue="analysis",
)
def legacy_run_analysis(self, assessment_id: str, candidate_id: str):
    task_id = self.request.id or f"legacy-analysis-{candidate_id}"
    acquired = task_registry.acquire_analysis_lock(candidate_id, task_id)
    if not acquired:
        return {"status": "skipped", "reason": "analysis_already_running"}

    try:
        LOGGER.info(
            "Running legacy analysis for candidate %s assessment %s",
            candidate_id,
            assessment_id,
        )

        # Touch the worker DB session early so workers fail fast on DB issues.
        with get_sync_db():
            pass

        # TODO(Task 3.x): Import the legacy analysis logic from
        # app/as_analysis/routes/analysis.py or its underlying service. Use
        # asyncio.run() to call async functions. The actual function to call is
        # the core logic of the /analysis/run endpoint.
        async def _run_placeholder() -> None:
            return None

        asyncio.run(_run_placeholder())
        return {"status": "complete", "candidate_id": candidate_id}
    except CeleryRetry:
        raise
    except Exception as exc:
        task_registry.release_analysis_lock(candidate_id)
        raise self.retry(exc=exc)
    finally:
        task_registry.release_analysis_lock(candidate_id)


def dispatch_legacy_analysis(assessment_id: str, candidate_id: str):
    if task_registry.is_analysis_running(candidate_id):
        return {"status": "already_running"}

    task = legacy_run_analysis.apply_async(
        args=[assessment_id, candidate_id],
        queue="analysis",
    )
    return str(task.id)
