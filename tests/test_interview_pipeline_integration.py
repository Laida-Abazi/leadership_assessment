import importlib
import sys
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


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
    yield MagicMock(name="integration_worker_db")


_fake_task_db.get_sync_db = _bootstrap_sync_db
sys.modules.setdefault("app.tasks.db", _fake_task_db)
sys.modules.setdefault(
    "app.services.cached_reads",
    importlib.import_module("app.services.cached_reads"),
)

from app.services.redis_client import redis_client
from app.services.task_registry import task_registry
from app.tasks.analysis import dispatch_analysis_chord


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def require_local_redis():
    try:
        redis_client.ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Redis must be running locally for integration tests: {exc}")


def test_signal_task_writes_to_redis_registry():
    task_registry.register_signal_task("seg_test", "cand_test", "task_test_id")

    result = task_registry.get_signal_task_ids("cand_test")

    assert "task_test_id" in result

    redis_client.delete("signal_task:seg_test", "signal_tasks_list:cand_test")


def test_analysis_lock_prevents_duplicate_runs():
    acquired1 = task_registry.acquire_analysis_lock("cand_dup", "task_001")
    acquired2 = task_registry.acquire_analysis_lock("cand_dup", "task_002")

    assert acquired1 is True
    assert acquired2 is False

    task_registry.release_analysis_lock("cand_dup")


def test_full_pipeline_chord_dispatch():
    captured = {}

    def fake_group(signatures):
        captured["signal_tasks"] = list(signatures)
        return captured["signal_tasks"]

    def fake_chord(grouped_tasks):
        captured["grouped_tasks"] = grouped_tasks

        def _dispatch(callback_signature):
            captured["callback_signature"] = callback_signature
            return SimpleNamespace(id="pipeline-123")

        return _dispatch

    with (
        patch(
            "app.tasks.signals.extract_signals.s",
            side_effect=lambda segment_id, candidate_id, assessment_id: {
                "segment_id": segment_id,
                "candidate_id": candidate_id,
                "assessment_id": assessment_id,
            },
        ),
        patch(
            "app.tasks.analysis.run_final_analysis.s",
            return_value={"task": "run_final_analysis"},
        ),
        patch("app.tasks.analysis.group", side_effect=fake_group),
        patch("app.tasks.analysis.chord", side_effect=fake_chord),
    ):
        pipeline_id = dispatch_analysis_chord(
            "cand_chord_test",
            "assess_test",
            ["seg_a", "seg_b", "seg_c"],
        )

    assert pipeline_id == "pipeline-123"
    assert task_registry.get_pipeline_id("cand_chord_test") is not None
    assert len(captured["signal_tasks"]) == 3

    redis_client.delete("pipeline:cand_chord_test")


def test_status_endpoint_never_triggers_compute(client):
    with (
        patch(
            "app.routers.intelligence._require_assessment",
            return_value=SimpleNamespace(id=1),
        ),
        patch("app.routers.intelligence.get_analysis_by_candidate", return_value=None),
        patch("app.routers.intelligence.task_registry.get_pipeline_id", return_value=None),
        patch("app.routers.intelligence.get_assessment_item_payloads", return_value=[]),
        patch("app.routers.intelligence.count_saved_answers", return_value=0),
        patch("app.tasks.analysis.dispatch_analysis_chord") as dispatch_mock,
    ):
        response = client.get(
            "/intelligence/assessment/1/status",
            params={"candidate_id": 1},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert dispatch_mock.call_count == 0


def test_assessment_creation_returns_202(client):
    with patch(
        "app.tasks.assessment.dispatch_linkedin_assessment_chain",
        return_value="fake_task_id",
    ):
        started_at = perf_counter()
        response = client.post(
            "/assessments/generate-from-linkedin-job",
            json={
                "linkedin_job_url": "https://www.linkedin.com/jobs/view/123456",
                "user_id": 1,
                "assessment_type_code": "leadership_core",
            },
        )
        elapsed_ms = (perf_counter() - started_at) * 1000

    assert response.status_code == 202
    assert response.json()["task_id"] == "fake_task_id"
    assert elapsed_ms < 500
