import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.as_requirements.config.models_setup import get_openai_client, MODEL_MINI
from app.db import get_db
from app.db.models import JobRequirements

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

    class Config:
        from_attributes = True


@router.post("/analyze", response_model=JobRequirementOut)
def analyze_and_insert_job_requirements(
    job_description: str = Form(..., description="Job description text; multi-line is supported"),
    db: Session = Depends(get_db),
):
    """Accept a job description, analyze it with GPT, extract requirements into one object, insert one row, and return that object."""
    job_id = str(uuid.uuid4())
    data = analyze_job_description(job_description)
    row = insert_job_requirements(db, job_id, data)
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
    )
