import asyncio
import logging
import os

from celery import chain
from celery.exceptions import Retry as CeleryRetry

try:
    from app.celery.celery_app import celery_app
except ImportError:  # pragma: no cover - compatibility with current package layout
    from app.celery.celery_app import celery_app

from app.services.cached_reads import invalidate_assessment_cache
from app.services.rate_limiter import gemini_rate_limiter
from app.tasks.db import get_sync_db


LOGGER = logging.getLogger(__name__)
WORKER_DATABASE_URL = os.getenv("DATABASE_URL", "")


@celery_app.task(
    name="tasks.scrape_linkedin",
    bind=True,
    max_retries=3,
    default_retry_delay=20,
    acks_late=True,
    queue="scraping",
)
def scrape_linkedin(self, linkedin_url: str, owner_user_id: str):
    try:
        LOGGER.info("Scraping LinkedIn URL for user %s", owner_user_id)

        # Touch the worker DB session early so workers fail fast on bad DB config.
        with get_sync_db():
            pass

        # TODO(Task 3.3): Wire the real LinkedIn scraping entry point here.
        # from app.services.brightdata_linkedin import scrape_job_sync
        # or asyncio.run(scrape_job(linkedin_url))
        if not WORKER_DATABASE_URL:
            LOGGER.warning(
                "DATABASE_URL is not set in the worker environment for user %s",
                owner_user_id,
            )

        raw_job_data = {
            "linkedin_url": linkedin_url,
            "owner_user_id": owner_user_id,
        }
        return raw_job_data
    except CeleryRetry:
        raise
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(
    name="tasks.analyze_job_requirements",
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    acks_late=True,
    queue="analysis",
)
def analyze_job_requirements(self, raw_job_data: dict, owner_user_id: str):
    try:
        if not gemini_rate_limiter.wait_for_slot():
            raise Exception("Rate limit exceeded")

        with get_sync_db():
            pass

        # TODO(Task 3.3): Wire the real job analysis entry point here.
        # from app.services.ai_analysis import analyze_job_sync
        # or asyncio.run(analyze_job(raw_job_data))
        job_requirement_id = str(raw_job_data.get("job_requirement_id", ""))
        return {"job_requirement_id": job_requirement_id}
    except CeleryRetry:
        raise
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(
    name="tasks.generate_assessment",
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    acks_late=True,
    queue="analysis",
)
def generate_assessment(
    self, job_analysis_result: dict | str, owner_user_id: str | None = None
):
    try:
        if isinstance(job_analysis_result, str) and owner_user_id is None:
            owner_user_id = job_analysis_result
            job_analysis_result = {}

        if owner_user_id is None:
            raise ValueError("owner_user_id is required for assessment generation")

        if not gemini_rate_limiter.wait_for_slot():
            raise Exception("Rate limit exceeded")

        with get_sync_db():
            pass

        # TODO(Task 3.3): Wire the real assessment generation entry point here.
        # from app.services.assessment_registry import generate_from_job_sync
        # or asyncio.run(...)
        assessment_id = str(job_analysis_result.get("assessment_id", ""))
        asyncio.run(invalidate_assessment_cache(assessment_id))
        return {
            "assessment_id": assessment_id,
            "job_requirement_id": str(job_analysis_result.get("job_requirement_id", "")),
        }
    except CeleryRetry:
        raise
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(
    name="tasks.generate_embeddings",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    acks_late=True,
    queue="embeddings",
)
def generate_embeddings(self, assessment_result: dict):
    try:
        with get_sync_db():
            pass

        # TODO(Task 3.3): Wire the real embedding generation entry point here.
        # from app.rag.embeddings import generate_context_embeddings_sync
        # or asyncio.run(...)
        LOGGER.info(
            "Assessment embeddings complete for assessment %s",
            assessment_result.get("assessment_id"),
        )
        return {
            "status": "embedded",
            "assessment_id": assessment_result["assessment_id"],
        }
    except CeleryRetry:
        raise
    except Exception as exc:
        raise self.retry(exc=exc)


def dispatch_linkedin_assessment_chain(linkedin_url: str, owner_user_id: str) -> str:
    workflow = chain(
        scrape_linkedin.s(linkedin_url, owner_user_id)
        | analyze_job_requirements.s(owner_user_id)
        | generate_assessment.s(owner_user_id)
        | generate_embeddings.s()
    )
    task = workflow.apply_async()
    return str(task.id)


def dispatch_direct_assessment_chain(owner_user_id: str) -> str:
    workflow = chain(
        generate_assessment.s(owner_user_id)
        | generate_embeddings.s()
    )
    task = workflow.apply_async()
    return str(task.id)


@celery_app.task(
    name="tasks.regenerate_assessment_embeddings",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    acks_late=True,
    queue="embeddings",
)
def regenerate_assessment_embeddings(self, assessment_id: str, reason: str = "manual"):
    try:
        LOGGER.info(
            "Regenerating embeddings for assessment %s, reason: %s",
            assessment_id,
            reason,
        )
        asyncio.run(invalidate_assessment_cache(assessment_id))

        # TODO(Task 3.3): Wire the real embedding regeneration entry point here.
        # from app.rag.embeddings import generate_context_embeddings_sync

        LOGGER.info("Assessment embedding regeneration complete for %s", assessment_id)
        return {"status": "re-embedded", "assessment_id": assessment_id}
    except CeleryRetry:
        raise
    except Exception as exc:
        raise self.retry(exc=exc)


def dispatch_embedding_regeneration(
    assessment_id: str, reason: str = "manual"
) -> str:
    task = regenerate_assessment_embeddings.apply_async(
        args=[assessment_id],
        kwargs={"reason": reason},
        queue="embeddings",
    )
    return str(task.id)
