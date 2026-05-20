"""Signal extraction task — runs in signal workers, consumes from the
'signals' queue. One task is dispatched per interview question completion."""

import logging
import os

from celery.exceptions import Retry as CeleryRetry

try:
    from app.celery.celery_app import celery_app
except ImportError:  # pragma: no cover - compatibility with current package layout
    from app.celery.celery_app import celery_app

from app.services.rate_limiter import gemini_rate_limiter
from app.services.task_registry import task_registry
from app.tasks.db import get_sync_db


LOGGER = logging.getLogger(__name__)
WORKER_DATABASE_URL = os.getenv("DATABASE_URL", "")


@celery_app.task(
    name="tasks.extract_signals",
    bind=True,
    max_retries=3,
    default_retry_delay=15,
    acks_late=True,
    queue="signals",
)
def extract_signals(self, segment_id: str, candidate_id: str, assessment_id: str):
    task_id = self.request.id or f"signals-{segment_id}"
    task_registry.register_signal_task(segment_id, candidate_id, task_id)

    try:
        if not gemini_rate_limiter.wait_for_slot():
            rate_limit_exc = Exception("Rate limit exceeded")
            if self.request.retries >= self.max_retries:
                LOGGER.error(
                    "Signal extraction failed permanently for segment %s: %s",
                    segment_id,
                    rate_limit_exc,
                )
                raise rate_limit_exc
            raise self.retry(countdown=30, exc=rate_limit_exc)

        # Touch the worker DB session early so workers fail fast on bad DB config
        # before spending model quota in the async extraction flow.
        with get_sync_db():
            pass

        from app.services.intelligence import extract_signals_for_segment_sync

        if not WORKER_DATABASE_URL:
            LOGGER.warning(
                "DATABASE_URL is not set in the worker environment for segment %s",
                segment_id,
            )

        extract_signals_for_segment_sync(segment_id, candidate_id, assessment_id)

        LOGGER.info("Signal extraction complete for segment %s", segment_id)
        return {"segment_id": segment_id, "status": "complete"}
    except CeleryRetry:
        raise
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            LOGGER.error(
                "Signal extraction failed permanently for segment %s: %s",
                segment_id,
                exc,
            )
            raise
        raise self.retry(exc=exc)
