import os

from celery.schedules import crontab
from kombu import Exchange, Queue


# Broker and backend connection settings are environment-driven so local,
# containerized, and deployed workers can use different infrastructure.
# Serialization settings keep task payloads and results in JSON with UTC time.
# Reliability settings control acknowledgment, prefetching, and requeue behavior.
# Queue and routing settings split critical-path work from background processing.
# Result and chord settings define retention and callback behavior.

from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

broker_url = os.environ.get(
    "CELERY_BROKER_URL", "amqp://devuser:devpass@localhost:5672//"
)
result_backend = os.environ.get(
    "CELERY_RESULT_BACKEND", "redis://localhost:6379/0"
)
broker_connection_retry = True
broker_connection_retry_on_startup = True

task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]
timezone = "UTC"
enable_utc = True

task_acks_late = True
worker_prefetch_multiplier = 1
task_reject_on_worker_lost = True

result_expires = 86400
task_ignore_result = False

signals_exchange = Exchange("signals", type="direct")
analysis_exchange = Exchange("analysis", type="direct")
embeddings_exchange = Exchange("embeddings", type="direct")
scraping_exchange = Exchange("scraping", type="direct")
dlx_exchange = Exchange("dlx", type="direct")

task_queues = (
    Queue(
        "signals",
        signals_exchange,
        routing_key="signals",
        queue_arguments={
            "x-dead-letter-exchange": "dlx",
            "x-dead-letter-routing-key": "dlq",
            "x-message-ttl": 1800000,
        },
    ),
    Queue(
        "analysis",
        analysis_exchange,
        routing_key="analysis",
        queue_arguments={
            "x-dead-letter-exchange": "dlx",
            "x-dead-letter-routing-key": "dlq",
            "x-message-ttl": 1800000,
        },
    ),
    Queue(
        "embeddings",
        embeddings_exchange,
        routing_key="embeddings",
    ),
    Queue(
        "scraping",
        scraping_exchange,
        routing_key="scraping",
    ),
    Queue(
        "dlq",
        dlx_exchange,
        routing_key="dlq",
    ),
)

task_routes = {
    "tasks.extract_signals": {"queue": "signals"},
    "tasks.run_final_analysis": {"queue": "analysis"},
    "tasks.generate_embeddings": {"queue": "embeddings"},
    "tasks.scrape_linkedin": {"queue": "scraping"},
    "tasks.analyze_job_requirements": {"queue": "analysis"},
    "tasks.generate_assessment": {"queue": "analysis"},
    "tasks.legacy_run_analysis": {"queue": "analysis"},
}

task_chord_propagates = False
task_default_queue = "signals"

beat_schedule = {
    "check-dlq-every-5-minutes": {
        "task": "tasks.check_dlq_depth",
        "schedule": 300.0,  # every 5 minutes
        # Equivalent cadence: crontab(minute="*/5")
    },
}
