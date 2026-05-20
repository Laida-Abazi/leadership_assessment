import logging
import os

from kombu import Connection

from app.celery.celery_app import celery_app


LOGGER = logging.getLogger(__name__)


@celery_app.task(name="tasks.check_dlq_depth")
def check_dlq_depth():
    broker_url = os.environ.get(
        "CELERY_BROKER_URL", "amqp://devuser:devpass@localhost:5672//"
    )

    try:
        with Connection(broker_url) as connection:
            with connection.SimpleQueue("dlq") as queue:
                count = queue.qsize()

        if count > 0:
            LOGGER.warning(
                "ALERT: %s failed tasks in dead letter queue. Check Flower for details.",
                count,
            )
        if count > 10:
            LOGGER.error(
                "CRITICAL: DLQ has %s messages. Analysis pipeline may be failing.",
                count,
            )

        return {"dlq_depth": count}
    except Exception as exc:
        LOGGER.warning("DLQ check failed because the broker is unreachable: %s", exc)
        return {"dlq_depth": None}


# To run the beat scheduler alongside workers:
# celery -A app.celery.celery_app beat --loglevel=info
# Or combined: celery -A app.celery.celery_app worker --beat --loglevel=info (dev only)
