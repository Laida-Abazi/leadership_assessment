from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.as_requirements.routes.ai_analysis import build_job_description_from_linkedin_payload
from app.services.brightdata_linkedin import (
    BrightDataError,
    fetch_linkedin_job_posting,
    validate_linkedin_job_url,
)


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_validate_linkedin_job_url_accepts_jobs_view_url():
    url = "https://www.linkedin.com/jobs/view/software-engineer-at-example-1234567890/"
    assert validate_linkedin_job_url(url) == url


def test_validate_linkedin_job_url_rejects_non_linkedin_domain():
    with pytest.raises(BrightDataError, match="Only LinkedIn job posting URLs are supported"):
        validate_linkedin_job_url("https://example.com/jobs/view/123")


def test_fetch_linkedin_job_posting_returns_first_payload_item(monkeypatch):
    monkeypatch.setenv("BRIGHT_DATA_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.services.brightdata_linkedin.urlopen",
        lambda request, timeout: _FakeResponse(
            [
                {
                    "url": "https://www.linkedin.com/jobs/view/software-engineer-at-example-1234567890/",
                    "job_title": "Software Engineer",
                    "company_name": "Example",
                }
            ]
        ),
    )

    result = fetch_linkedin_job_posting(
        "https://www.linkedin.com/jobs/view/software-engineer-at-example-1234567890/"
    )

    assert result["job_title"] == "Software Engineer"
    assert result["company_name"] == "Example"


def test_build_job_description_from_linkedin_payload_strips_html():
    payload = {
        "job_title": "Software Engineer",
        "company_name": "Example",
        "job_location": "Remote",
        "job_employment_type": "Full-time",
        "job_description_formatted": "<section><strong>Responsibilities</strong><ul><li>Build APIs</li></ul></section>",
    }

    result = build_job_description_from_linkedin_payload(payload)

    assert "Job title: Software Engineer" in result
    assert "Company: Example" in result
    assert "Employment type: Full-time" in result
    assert "Responsibilities Build APIs" in result


def test_build_job_description_from_linkedin_payload_requires_description():
    with pytest.raises(HTTPException) as exc:
        build_job_description_from_linkedin_payload(
            {
                "job_title": "Software Engineer",
                "company_name": "Example",
                "job_location": "Remote",
            }
        )

    assert exc.value.status_code == 502
