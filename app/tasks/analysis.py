import asyncio
import logging

from celery import chord, group
from celery.exceptions import Retry as CeleryRetry

try:
    from app.celery.celery_app import celery_app
except ImportError:  # pragma: no cover - compatibility with current package layout
    from app.celery.celery_app import celery_app

from app.db.models import Assessments
from app.services.cached_reads import invalidate_job_requirement_profile
from app.services.rate_limiter import gemini_rate_limiter
from app.services.task_registry import task_registry
from app.tasks.db import get_sync_db


LOGGER = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.run_final_analysis",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
    queue="analysis",
)
def run_final_analysis(self, signal_results: list, candidate_id: str, assessment_id: str):
    failed_count = sum(1 for r in signal_results if isinstance(r, Exception))
    if failed_count > 0:
        LOGGER.warning(
            "%s signal tasks failed, proceeding with partial data for candidate %s",
            failed_count,
            candidate_id,
        )

    task_id = self.request.id or f"analysis-{candidate_id}"
    acquired = task_registry.acquire_analysis_lock(candidate_id, task_id)
    if not acquired:
        LOGGER.info("Analysis already running for candidate %s, skipping", candidate_id)
        return {"status": "skipped", "reason": "lock_held"}

    try:
        if not gemini_rate_limiter.wait_for_slot():
            raise Exception("Rate limit exceeded")

        # Touch the worker DB session early so workers fail fast on DB issues.
        with get_sync_db() as db:
            assessment = db.get(Assessments, int(assessment_id))
            job_req_id = (
                str(assessment.job_requirements_id)
                if assessment and assessment.job_requirements_id is not None
                else None
            )

        from app.services.intelligence import run_full_analysis_chained_sync

        run_full_analysis_chained_sync(candidate_id, assessment_id)
        if job_req_id is not None:
            # Profile may have been rebuilt during analysis — invalidate profile cache
            asyncio.run(invalidate_job_requirement_profile(job_req_id))

        task_registry.clear_candidate_tasks(candidate_id)
        LOGGER.info("Final analysis complete for candidate %s", candidate_id)
        return {"status": "completed", "candidate_id": candidate_id}
    except CeleryRetry:
        raise
    except Exception as exc:
        task_registry.release_analysis_lock(candidate_id)
        raise self.retry(exc=exc)
    finally:
        task_registry.release_analysis_lock(candidate_id)


def dispatch_analysis_chord(
    candidate_id: str, assessment_id: str, segment_ids: list[str]
) -> str:
    from app.tasks.signals import extract_signals

    pipeline = chord(
        group(extract_signals.s(sid, candidate_id, assessment_id) for sid in segment_ids)
    )(
        run_final_analysis.s(
            candidate_id=candidate_id,
            assessment_id=assessment_id,
        )
    )

    pipeline_id = str(pipeline.id)
    task_registry.set_pipeline_id(candidate_id, pipeline_id)
    return pipeline_id
