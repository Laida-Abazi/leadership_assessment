import importlib
import sys
from pathlib import Path
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from celery.contrib.pytest import celery_app, celery_config


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


sys.modules.setdefault(
    "app.celery_config",
    importlib.import_module("app.celery.celery_config"),
)

_fake_task_db = ModuleType("app.tasks.db")


@contextmanager
def _bootstrap_sync_db():
    yield MagicMock(name="bootstrap_db_session")


_fake_task_db.get_sync_db = _bootstrap_sync_db
sys.modules.setdefault("app.tasks.db", _fake_task_db)

_fake_models = ModuleType("app.db.models")


class _AssessmentModel:
    pass


_fake_models.Assessments = _AssessmentModel
sys.modules.setdefault("app.db.models", _fake_models)

_fake_cached_reads = ModuleType("app.services.cached_reads")


async def _invalidate_job_requirement_profile(_job_requirement_id):
    return None


_fake_cached_reads.invalidate_job_requirement_profile = _invalidate_job_requirement_profile
sys.modules.setdefault("app.services.cached_reads", _fake_cached_reads)

from app.celery.celery_app import celery_app as project_celery_app
from app.services.rate_limiter import gemini_rate_limiter
from app.services.task_registry import task_registry
from app.tasks.analysis import run_final_analysis
from app.tasks.signals import extract_signals

EAGER_CELERY_CONFIG = {
    "task_always_eager": True,
    "task_eager_propagates": True,
    "broker_url": "memory://",
    "result_backend": "cache+memory://",
}


@pytest.fixture(scope="module")
def celery_config():
    return EAGER_CELERY_CONFIG


@pytest.fixture(scope="module")
def celery_parameters():
    return {}


@pytest.fixture(autouse=True)
def configure_project_celery():
    project_celery_app.conf.update(EAGER_CELERY_CONFIG)
    yield


def _db_context(*, job_requirements_id=None):
    @contextmanager
    def _context():
        session = MagicMock(name="sync_db_session")
        session.get.return_value = SimpleNamespace(job_requirements_id=job_requirements_id)
        yield session

    return _context


def test_extract_signals_task_success():
    fake_intelligence = SimpleNamespace(
        extract_signals_for_segment_sync=MagicMock(return_value=None)
    )

    with (
        patch("app.tasks.signals.task_registry.register_signal_task"),
        patch("app.tasks.signals.gemini_rate_limiter.wait_for_slot", return_value=True),
        patch("app.tasks.signals.get_sync_db", _db_context()),
        patch.dict(sys.modules, {"app.services.intelligence": fake_intelligence}),
    ):
        result = extract_signals.apply(args=["seg_1", "cand_1", "assess_1"])

    assert result.status == "SUCCESS"
    assert result.result["segment_id"] == "seg_1"


def test_extract_signals_retries_on_failure():
    fake_intelligence = SimpleNamespace(
        extract_signals_for_segment_sync=MagicMock(
            side_effect=Exception("Gemini timeout")
        )
    )

    with (
        patch("app.tasks.signals.task_registry.register_signal_task"),
        patch("app.tasks.signals.gemini_rate_limiter.wait_for_slot", return_value=True),
        patch("app.tasks.signals.get_sync_db", _db_context()),
        patch.dict(sys.modules, {"app.services.intelligence": fake_intelligence}),
    ):
        with pytest.raises(Exception, match="Gemini timeout"):
            extract_signals.apply(args=["seg_1", "cand_1", "assess_1"])


def test_run_final_analysis_acquires_lock():
    fake_intelligence = SimpleNamespace(
        run_full_analysis_chained_sync=MagicMock(return_value=None)
    )

    with (
        patch(
            "app.tasks.analysis.task_registry.acquire_analysis_lock", return_value=True
        ) as acquire_lock,
        patch("app.tasks.analysis.task_registry.release_analysis_lock") as release_lock,
        patch("app.tasks.analysis.task_registry.clear_candidate_tasks"),
        patch("app.tasks.analysis.gemini_rate_limiter.wait_for_slot", return_value=True),
        patch("app.tasks.analysis.get_sync_db", _db_context(job_requirements_id=123)),
        patch(
            "app.tasks.analysis.invalidate_job_requirement_profile",
            MagicMock(return_value=None),
        ),
        patch("app.tasks.analysis.asyncio.run", return_value=None),
        patch.dict(sys.modules, {"app.services.intelligence": fake_intelligence}),
    ):
        result = run_final_analysis.apply(
            args=[[{"status": "complete"}], "cand_1", "1"]
        )

    assert result.status == "SUCCESS"
    assert result.result["status"] == "completed"
    acquire_lock.assert_called_once()
    assert acquire_lock.call_args.args[0] == "cand_1"
    release_lock.assert_called_once_with("cand_1")


def test_run_final_analysis_skips_if_lock_held():
    with (
        patch(
            "app.tasks.analysis.task_registry.acquire_analysis_lock", return_value=False
        ),
        patch("app.tasks.analysis.asyncio.run") as asyncio_run,
    ):
        result = run_final_analysis.apply(args=[[], "cand_1", "1"])

    assert result.result == {"status": "skipped", "reason": "lock_held"}
    asyncio_run.assert_not_called()


def test_task_registry_cross_process_isolation():
    with (
        patch("app.services.task_registry.redis_client.set") as redis_set,
        patch("app.services.task_registry.redis_client.rpush") as redis_rpush,
        patch("app.services.task_registry.redis_client.expire"),
    ):
        task_registry.register_signal_task("seg_1", "cand_1", "task_abc")

    redis_set.assert_called_once_with("signal_task:seg_1", "task_abc", ex=3600)
    redis_rpush.assert_called_once_with("signal_tasks_list:cand_1", "task_abc")


def test_rate_limiter_shared_across_calls():
    with patch(
        "app.services.rate_limiter.redis_client.incr", side_effect=[61, 30]
    ):
        assert gemini_rate_limiter.check_and_increment() is False
        assert gemini_rate_limiter.check_and_increment() is True
