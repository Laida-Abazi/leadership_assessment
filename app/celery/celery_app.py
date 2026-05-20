"""This is the central Celery application instance. Import celery_app from here in tasks and worker startup commands."""

from celery import Celery
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

celery_app = Celery("leadership_assessment")
celery_app.config_from_object("app.celery.celery_config")
celery_app.autodiscover_tasks(
    [
        "app.tasks.signals",
        "app.tasks.analysis",
        "app.tasks.assessment",
        "app.tasks.legacy",
        "app.tasks.monitoring",
    ]
)
