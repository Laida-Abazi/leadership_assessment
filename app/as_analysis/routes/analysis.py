"""
Fits/gaps analysis of candidate responses vs job requirements and final hire prediction.
"""
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.as_requirements.config.models_setup import get_openai_client, MODEL_FULL
from app.db import get_db
from app.db.models import Assessments, JobRequirements, Responses, Analysis, Predictions
from app.services.assessment_persistence import iter_assessment_answers

router = APIRouter(prefix="/analysis", tags=["analysis"])

# Must match assessment question columns (and response columns: _question -> _response)
ASSESSMENT_QUESTION_FIELDS = [
    "behavioral_question",
    "competency_based_question",
    "situational_question",
    "panel_question",
    "business_case_question",
    "live_simulation_question",
    "psychometric_question",
    "structured_reference_question",
    "culture_alignment_question",
    "integrity_ethics_question",
]

FITS_GAPS_PROMPT = """You are an expert leadership assessor. You will be given:
1) The job requirements for a leadership position.
2) A set of interview questions that were asked and the candidate's responses.

Your task is to analyze each question–response pair against the job requirements and produce:
A) A detailed gap analysis: for each major competency/theme, explain evidence of fit, specific gaps, risk implications, and what additional evidence would increase confidence.
B) A final internal prediction on whether this leader is right for the position.

Job requirements:
---
{job_summary}
---

Interview Q&A (question type, question, candidate response):
---
{qa_block}
---

Return ONLY a valid JSON object with exactly these keys (no other text):
- "fits_and_gaps": (string) A detailed, structured narrative with section-like flow. Include concrete evidence from answers, missing evidence, severity of each gap, and practical implications for role success.
- "verdict": (string) Exactly one of: STRONG_FIT, FIT, MODERATE_FIT, WEAK_FIT, NOT_RECOMMENDED.
- "rationale": (string) A concise explanation for the verdict that references the most critical evidence and gaps.

Return only the JSON object."""


def _job_requirements_to_summary(job: JobRequirements) -> str:
    """Build a concise text summary of job requirements for the prompt."""
    parts = []
    if job.requirement:
        parts.append(f"Summary/Requirement: {job.requirement}")
    if job.skill:
        parts.append(f"Skills: {job.skill}")
    if job.soft_skill:
        parts.append(f"Soft skills: {job.soft_skill}")
    if job.experience:
        parts.append(f"Experience: {job.experience}")
    if job.education:
        parts.append(f"Education: {job.education}")
    if job.certification:
        parts.append(f"Certification: {job.certification}")
    if job.responsibility:
        parts.append(f"Responsibilities: {job.responsibility}")
    if job.seniority_level:
        parts.append(f"Seniority: {job.seniority_level}")
    if job.culture_fit:
        parts.append(f"Culture fit: {job.culture_fit}")
    if job.industry_experience:
        parts.append(f"Industry experience: {job.industry_experience}")
    if job.role_experience:
        parts.append(f"Role experience: {job.role_experience}")
    if job.language:
        parts.append(f"Language: {job.language}")
    if job.location:
        parts.append(f"Location: {job.location}")
    if job.availability:
        parts.append(f"Availability: {job.availability}")
    if job.work_authorization:
        parts.append(f"Work authorization: {job.work_authorization}")
    return "\n".join(parts) if parts else "No structured requirements provided."


def _build_qa_block(
    db: Session,
    assessment: Assessments,
    responses: Responses | None,
    *,
    candidate_id: int | None = None,
) -> str:
    """Build a single text block of question type, question, and response for each answered question."""
    del responses  # Canonical answers are the source of truth when available.
    lines = []
    for payload in iter_assessment_answers(db, assessment, candidate_id=candidate_id):
        question = payload.get("question_text")
        answer = payload.get("answer_text") or "(No response)"
        if not question:
            continue
        label = payload.get("display_label") or payload.get("item_key", "Question").replace("_", " ").title()
        lines.append(f"[{label}]\nQ: {question.strip()}\nA: {answer.strip()}\n")
    return "\n".join(lines) if lines else "No responses recorded."


def _run_fits_gaps_and_prediction(job_summary: str, qa_block: str) -> dict[str, Any]:
    """Call LLM to produce fits_and_gaps text, verdict, and rationale. Returns dict with those keys."""
    client = get_openai_client()
    prompt = FITS_GAPS_PROMPT.format(job_summary=job_summary, qa_block=qa_block)
    response = client.chat.completions.create(
        model=MODEL_FULL,
        messages=[{"role": "user", "content": prompt}],
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
    fits_and_gaps = data.get("fits_and_gaps")
    verdict = data.get("verdict")
    rationale = data.get("rationale")
    if not isinstance(fits_and_gaps, str):
        fits_and_gaps = str(fits_and_gaps) if fits_and_gaps else ""
    if not isinstance(verdict, str):
        verdict = "MODERATE_FIT"
    if not isinstance(rationale, str):
        rationale = str(rationale) if rationale else ""
    return {
        "fits_and_gaps": fits_and_gaps,
        "verdict": verdict.strip().upper(),
        "rationale": rationale,
    }


def _save_analysis_and_prediction(
    db: Session,
    job_requirements_id: int,
    assessment_id: int,
    candidate_id: int | None,
    responses_id: int,
    analysis_text: str,
    verdict: str,
    rationale: str,
) -> tuple[Analysis, Predictions]:
    """Create Analysis row and Predictions row. Returns (analysis_row, prediction_row)."""
    analysis_row = Analysis(
        job_requirements_id=job_requirements_id,
        assessment_id=assessment_id,
        candidate_id=candidate_id,
        responses_id=responses_id,
        analysis=analysis_text,
    )
    db.add(analysis_row)
    db.commit()
    db.refresh(analysis_row)
    prediction_text = f"{verdict}\n\n{rationale}".strip()
    prediction_row = Predictions(analysis_id=analysis_row.id, prediction=prediction_text)
    db.add(prediction_row)
    db.commit()
    db.refresh(prediction_row)
    return analysis_row, prediction_row


class RunAnalysisRequest(BaseModel):
    assessment_id: int
    candidate_id: int | None = None


class RunAnalysisResponse(BaseModel):
    analysis_id: int
    prediction_id: int
    fits_and_gaps: str
    verdict: str
    rationale: str

    class Config:
        from_attributes = True


@router.post("/run", response_model=RunAnalysisResponse)
def run_analysis(
    body: RunAnalysisRequest,
    db: Session = Depends(get_db),
):
    """
    Load the assessment, its responses, and job requirements; analyze fits and gaps
    between the candidate's responses and the role; produce a final prediction on
    whether the leader is right for the position. Persist results to Analysis and
    Predictions and return them.
    """
    assessment = db.get(Assessments, body.assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    job = assessment.job_requirements
    if not job:
        raise HTTPException(status_code=404, detail="Job requirements not found for this assessment")
    responses_query = db.query(Responses).filter(Responses.assessment_id == body.assessment_id)
    if body.candidate_id is None:
        responses_query = responses_query.filter(Responses.candidate_id.is_(None))
    else:
        responses_query = responses_query.filter(Responses.candidate_id == body.candidate_id)
    responses = responses_query.first()
    if not responses:
        raise HTTPException(
            status_code=400,
            detail="No responses found for this assessment. Complete the interview before running analysis.",
        )

    job_summary = _job_requirements_to_summary(job)
    qa_block = _build_qa_block(db, assessment, responses, candidate_id=body.candidate_id)
    result = _run_fits_gaps_and_prediction(job_summary, qa_block)

    analysis_row, prediction_row = _save_analysis_and_prediction(
        db,
        job_requirements_id=job.id,
        assessment_id=assessment.id,
        candidate_id=body.candidate_id,
        responses_id=responses.id,
        analysis_text=result["fits_and_gaps"],
        verdict=result["verdict"],
        rationale=result["rationale"],
    )
    return RunAnalysisResponse(
        analysis_id=analysis_row.id,
        prediction_id=prediction_row.id,
        fits_and_gaps=result["fits_and_gaps"],
        verdict=result["verdict"],
        rationale=result["rationale"],
    )


class GetAnalysisResponse(BaseModel):
    analysis_id: int
    fits_and_gaps: str
    verdict: str
    rationale: str


@router.get("/assessment/{assessment_id}", response_model=GetAnalysisResponse)
def get_latest_analysis_for_assessment(
    assessment_id: int,
    candidate_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Return the most recent analysis and prediction for an assessment, if any."""
    assessment = db.get(Assessments, assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    analysis_row = (
        db.query(Analysis)
        .filter(
            Analysis.assessment_id == assessment_id,
            Analysis.candidate_id.is_(None) if candidate_id is None else Analysis.candidate_id == candidate_id,
        )
        .order_by(Analysis.id.desc())
        .first()
    )
    if not analysis_row:
        raise HTTPException(status_code=404, detail="No analysis found for this assessment")
    pred = (
        db.query(Predictions).filter(Predictions.analysis_id == analysis_row.id).first()
    )
    if not pred:
        raise HTTPException(status_code=404, detail="No prediction found for this analysis")
    # Parse verdict (first line) and rationale (rest)
    parts = (pred.prediction or "").strip().split("\n", 1)
    verdict = parts[0].strip() if parts else ""
    rationale = parts[1].strip() if len(parts) > 1 else ""
    return GetAnalysisResponse(
        analysis_id=analysis_row.id,
        fits_and_gaps=analysis_row.analysis or "",
        verdict=verdict,
        rationale=rationale,
    )
