from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.services.intelligence as intelligence
from app.auth.candidate_access import (
    CANDIDATE_ACCESS_COOKIE_NAME,
    create_candidate_session_token,
    decode_candidate_session_token,
    require_candidate_assessment_access,
)
from app.services.interview_links import (
    build_interview_link_url,
    compute_token_hash,
    consume_assessment_access_link,
    format_public_link_token,
    get_link_status,
    parse_public_link_token,
    utcnow,
)


class _FakeLinkQuery:
    def __init__(self, link):
        self.link = link

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.link


class _FakeSession:
    def __init__(self, link):
        self.link = link
        self.rollback_count = 0
        self.commit_count = 0

    def query(self, _model):
        return _FakeLinkQuery(self.link)

    def add(self, _obj):
        return None

    def commit(self):
        self.commit_count += 1

    def refresh(self, _obj):
        return None

    def rollback(self):
        self.rollback_count += 1


def _build_link(secret: str, *, expires_at=None):
    salt = "testsalt"
    now = utcnow()
    return SimpleNamespace(
        id=7,
        assessment_id=99,
        token_salt=salt,
        token_hash=compute_token_hash(secret, salt),
        revoked_at=None,
        expires_at=expires_at or (now + timedelta(hours=1)),
        used_at=None,
        use_count=0,
        max_uses=1,
        used_by_fingerprint=None,
        updated_at=None,
        created_at=now,
        candidate_email=None,
    )


def test_public_link_token_round_trip():
    token = format_public_link_token(42, "secret-value")
    assert parse_public_link_token(token) == (42, "secret-value")


def test_build_interview_link_url_uses_app_url(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://example.test")
    assert build_interview_link_url("42.secret") == "https://example.test/candidate/interview/42.secret"


def test_consume_assessment_access_link_marks_link_used_and_rejects_reuse(monkeypatch):
    monkeypatch.setenv("ENABLE_ONE_TIME_INTERVIEW_LINKS", "true")
    secret = "candidate-secret"
    link = _build_link(secret)
    session = _FakeSession(link)
    raw_token = format_public_link_token(link.id, secret)

    consumed = consume_assessment_access_link(session, raw_token, fingerprint="fingerprint")

    assert consumed.use_count == 1
    assert consumed.used_at is not None
    assert consumed.used_by_fingerprint == "fingerprint"
    assert session.commit_count == 1

    with pytest.raises(HTTPException) as exc:
        consume_assessment_access_link(session, raw_token, fingerprint="fingerprint")
    assert exc.value.status_code == 404


def test_get_link_status_prioritizes_used_before_revoked_or_expired():
    link = _build_link("secret")
    assert get_link_status(link) == "active"

    link.revoked_at = utcnow()
    assert get_link_status(link) == "revoked"

    link.revoked_at = None
    link.expires_at = utcnow() - timedelta(seconds=1)
    assert get_link_status(link) == "expired"

    link.used_at = utcnow()
    assert get_link_status(link) == "used"


def test_candidate_session_token_round_trip():
    token = create_candidate_session_token(assessment_id=18, link_id=5)
    context = decode_candidate_session_token(token)
    assert context.assessment_id == 18
    assert context.link_id == 5


def test_candidate_assessment_guard_rejects_cross_assessment_access():
    token = create_candidate_session_token(assessment_id=18, link_id=5)
    connection = SimpleNamespace(
        headers={},
        cookies={CANDIDATE_ACCESS_COOKIE_NAME: token},
        query_params={},
    )

    with pytest.raises(HTTPException) as exc:
        require_candidate_assessment_access(assessment_id=99, connection=connection)
    assert exc.value.status_code == 403


def test_schedule_final_analysis_retries_once_and_completes(monkeypatch):
    intelligence._ANALYSIS_TASKS.clear()
    intelligence._ANALYSIS_STATE.clear()

    calls = {"count": 0}

    async def fake_wait(*args, **kwargs):
        return None

    async def fake_run(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("transient")

    monkeypatch.setattr(intelligence, "_await_registered_signal_tasks", fake_wait)
    monkeypatch.setattr(intelligence, "run_full_analysis_chained", fake_run)

    async def scenario():
        scheduled = intelligence.schedule_final_analysis(
            assessment_id=1,
            job_requirements_id=2,
            retry_attempts=1,
            retry_delay_seconds=0,
        )
        assert scheduled is True
        await intelligence._ANALYSIS_TASKS[1]
        assert calls["count"] == 2
        assert intelligence._ANALYSIS_STATE[1]["status"] == "completed"
        assert intelligence._ANALYSIS_STATE[1]["attempts"] == 2

    asyncio.run(scenario())


def test_schedule_final_analysis_marks_failed_after_exhausting_retries(monkeypatch):
    intelligence._ANALYSIS_TASKS.clear()
    intelligence._ANALYSIS_STATE.clear()

    async def fake_wait(*args, **kwargs):
        return None

    async def fake_run(**kwargs):
        raise RuntimeError("fatal")

    monkeypatch.setattr(intelligence, "_await_registered_signal_tasks", fake_wait)
    monkeypatch.setattr(intelligence, "run_full_analysis_chained", fake_run)

    async def scenario():
        scheduled = intelligence.schedule_final_analysis(
            assessment_id=77,
            job_requirements_id=3,
            retry_attempts=0,
            retry_delay_seconds=0,
        )
        assert scheduled is True
        await intelligence._ANALYSIS_TASKS[77]
        assert intelligence._ANALYSIS_STATE[77]["status"] == "failed"
        assert intelligence._ANALYSIS_STATE[77]["attempts"] == 1
        assert "fatal" in intelligence._ANALYSIS_STATE[77]["last_error"]

    asyncio.run(scenario())
