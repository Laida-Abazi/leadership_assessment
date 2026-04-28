import json
import re
import uuid
from html import unescape
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.as_requirements.config.models_setup import get_openai_client, MODEL_MINI
from app.db import get_db
from app.db.models import JobRequirements
from app.services.brightdata_linkedin import BrightDataError, fetch_linkedin_job_posting

router = APIRouter(prefix="/job-requirements", tags=["job-requirements"])

# Keys for the single extracted object (no category)
JOB_REQUIREMENT_KEYS = [
    "requirement",
    "skill",
    "soft_skill",
    "experience",
    "education",
    "certification",
    "responsibility",
    "language",
    "industry_experience",
    "role_experience",
    "location",
    "availability",
    "work_authorization",
    "seniority_level",
    "culture_fit",
]

EXTRACTION_PROMPT = """Analyze the following job description and extract all requirements and qualifications into a single JSON object.
Use exactly these keys (use null for any not mentioned): requirement, skill, soft_skill, experience, education, certification, responsibility, language, industry_experience, role_experience, location, availability, work_authorization, seniority_level, culture_fit.

- "requirement" (string): a concise summary of the main requirement or the full description text.
- For the rest, put the extracted value as a string, or null if not mentioned. You may combine multiple items into one string (e.g. comma-separated) where appropriate.

Return only the JSON object, no other text."""


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def build_job_description_from_linkedin_payload(payload: dict[str, Any]) -> str:
    """Turn Bright Data's LinkedIn job payload into extractor-friendly plain text."""
    header_lines = [
        f"Job title: {_clean_text(payload.get('job_title')) or 'Unknown'}",
        f"Company: {_clean_text(payload.get('company_name')) or 'Unknown'}",
        f"Location: {_clean_text(payload.get('job_location')) or 'Unknown'}",
    ]

    optional_lines = [
        ("Seniority level", payload.get("seniority_level")),
        ("Job function", payload.get("job_function")),
        ("Employment type", payload.get("job_employment_type")),
        ("Industries", payload.get("job_industries")),
        ("Base pay range", payload.get("job_base_pay_range")),
        ("Posted", payload.get("job_posted_time")),
    ]
    for label, value in optional_lines:
        cleaned = _clean_text(value)
        if cleaned:
            header_lines.append(f"{label}: {cleaned}")

    summary = _clean_text(payload.get("job_summary"))
    formatted_description = _clean_text(payload.get("job_description_formatted"))
    description = formatted_description or summary
    if not description:
        raise HTTPException(
            status_code=502,
            detail="Bright Data returned the job post without a usable description.",
        )

    return "\n".join(
        [
            *header_lines,
            "",
            "Job description:",
            description,
        ]
    ).strip()


def analyze_job_description(job_description: str) -> dict[str, Any]:
    """Use GPT to extract job requirements into a single object. Returns one dict suitable for one job_requirements row."""
    client = get_openai_client()
    response = client.chat.completions.create(
        model=MODEL_MINI,
        messages=[
            {"role": "user", "content": f"{EXTRACTION_PROMPT}\n\nJob description:\n{job_description}"},
        ],
    )
    content = (response.choices[0].message.content or "").strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"LLM returned invalid JSON: {e}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="LLM did not return a JSON object")
    normalized: dict[str, Any] = {}
    for key in JOB_REQUIREMENT_KEYS:
        val = data.get(key)
        if key == "requirement":
            normalized[key] = (val if isinstance(val, str) else str(val)) if val else ""
        elif val is not None and isinstance(val, str):
            normalized[key] = val
        else:
            normalized[key] = str(val) if val is not None else None
    if not normalized.get("requirement"):
        normalized["requirement"] = job_description[:2000] or "Job requirements"
    return normalized


def insert_job_requirements(
    db: Session,
    job_id: str,
    data: dict[str, Any],
) -> JobRequirements:
    """Insert the single extracted object as one row in job_requirements. Returns the created row."""
    row = JobRequirements(
        job_id=job_id,
        requirement=data.get("requirement", ""),
        skill=data.get("skill"),
        soft_skill=data.get("soft_skill"),
        experience=data.get("experience"),
        education=data.get("education"),
        certification=data.get("certification"),
        responsibility=data.get("responsibility"),
        language=data.get("language"),
        industry_experience=data.get("industry_experience"),
        role_experience=data.get("role_experience"),
        location=data.get("location"),
        availability=data.get("availability"),
        work_authorization=data.get("work_authorization"),
        seniority_level=data.get("seniority_level"),
        culture_fit=data.get("culture_fit"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class JobRequirementOut(BaseModel):
    id: int
    job_id: str
    requirement: str
    skill: str | None
    soft_skill: str | None
    experience: str | None
    education: str | None
    certification: str | None
    responsibility: str | None
    language: str | None
    industry_experience: str | None
    role_experience: str | None
    location: str | None
    availability: str | None
    work_authorization: str | None
    seniority_level: str | None
    culture_fit: str | None
    source: str | None = None
    linkedin_job_url: str | None = None
    linkedin_job_title: str | None = None
    linkedin_company_name: str | None = None
    linkedin_job_location: str | None = None

    class Config:
        from_attributes = True


class LinkedInJobAnalyzeRequest(BaseModel):
    linkedin_job_url: str


def serialize_job_requirement(
    row: JobRequirements,
    **extra_fields: Any,
) -> JobRequirementOut:
    return JobRequirementOut(
        id=row.id,
        job_id=row.job_id,
        requirement=row.requirement,
        skill=row.skill,
        soft_skill=row.soft_skill,
        experience=row.experience,
        education=row.education,
        certification=row.certification,
        responsibility=row.responsibility,
        language=row.language,
        industry_experience=row.industry_experience,
        role_experience=row.role_experience,
        location=row.location,
        availability=row.availability,
        work_authorization=row.work_authorization,
        seniority_level=row.seniority_level,
        culture_fit=row.culture_fit,
        **extra_fields,
    )


@router.post("/analyze", response_model=JobRequirementOut)
def analyze_and_insert_job_requirements(
    job_description: str = Form(..., description="Job description text; multi-line is supported"),
    db: Session = Depends(get_db),
):
    """Accept a job description, analyze it with GPT, extract requirements into one object, insert one row, and return that object."""
    job_id = str(uuid.uuid4())
    data = analyze_job_description(job_description)
    row = insert_job_requirements(db, job_id, data)
    return serialize_job_requirement(row)


@router.post("/analyze-linkedin-url", response_model=JobRequirementOut)
def analyze_and_insert_linkedin_job_requirements(
    body: LinkedInJobAnalyzeRequest,
    db: Session = Depends(get_db),
):
    """
    Fetch a LinkedIn job posting through Bright Data, extract structured
    requirements with GPT, persist them, and return the created row.
    """
    try:
        posting = fetch_linkedin_job_posting(body.linkedin_job_url)
    except BrightDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    job_description = build_job_description_from_linkedin_payload(posting)
    analyzed = analyze_job_description(job_description)
    job_id = str(posting.get("job_posting_id") or uuid.uuid4())
    row = insert_job_requirements(db, job_id, analyzed)
    return serialize_job_requirement(
        row,
        source="linkedin_brightdata",
        linkedin_job_url=_clean_text(posting.get("url")) or body.linkedin_job_url.strip(),
        linkedin_job_title=_clean_text(posting.get("job_title")),
        linkedin_company_name=_clean_text(posting.get("company_name")),
        linkedin_job_location=_clean_text(posting.get("job_location")),
    )
