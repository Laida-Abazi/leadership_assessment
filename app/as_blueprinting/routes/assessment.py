import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.as_requirements.config.models_setup import get_openai_client, MODEL_FULL
from app.db import get_db
from app.db.models import Assessments, JobRequirements
from app.rag.embeddings import get_context_for_agent, index_assessment

router = APIRouter(prefix="/assessments", tags=["assessments"])

# Assessment table question columns (one question per type)
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

ASSESSMENT_GENERATION_PROMPT = """You are an expert in leadership and hiring assessments. Given the following job requirements, generate exactly 10 assessment questions—one for each type below. Each question must be tailored to this specific role and its requirements. 

    Make every question sound natural and conversational, like something a friendly interviewer would say in a real chat. 
    Keep them clear, professional, and engaging for a formal assessment—avoid any "e.g.", lists like A/B/1/2, or catalog-style phrasing.
    Just craft smooth, human-sounding questions ready to read aloud.

    Return ONLY a valid JSON object with exactly these keys (use the key names as given); no other text.

Keys and meaning:
- behavioral_question: Focus on their past behavior and how they handled real situations, like "Tell me about a time when you faced a tough team challenge.
- competency_based_question: Probe specific competencies they've demonstrated, like "Walk me through how you've led a project from start to finish.
- situational_question: Present a hypothetical scenario, like "Imagine your team misses a key deadline—what would you do next?
- panel_question: Design for a group of interviewers, like "Our panel would love to hear your thoughts on balancing short-term wins with long-term goals.
- business_case_question: Test analysis and decision-making, like "Here's a business challenge we're facing—how would you approach solving it?
- live_simulation_question: Set up a real-time scenario or role-play, like "Pretend I'm a frustrated stakeholder—convince me to approve your budget.
- psychometric_question: Explore traits, preferences, or fit, like "How do you prefer to handle conflict in a high-pressure environment?
- structured_reference_question: uide checking references, like "Who would you suggest we speak with about your leadership in past roles, and what should we ask them?
- culture_alignment_question: Assess values and team fit, like "What draws you to our company's collaborative culture?
- integrity_ethics_question: Gauge ethical judgment, like "Describe a time when you had to make a tough call between business needs and doing what's right.
Job requirements to tailor questions to:
---
{job_summary}
---

Return only the JSON object with the 10 keys above, each value a single question string."""


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


def generate_assessment_questions(job: JobRequirements) -> dict[str, str]:
    """Use GPT-5.2 to generate 10 tailored assessment questions from job requirements. Returns a dict mapping field names to question strings."""
    client = get_openai_client()
    job_summary = _job_requirements_to_summary(job)
    prompt = ASSESSMENT_GENERATION_PROMPT.format(job_summary=job_summary)

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

    result: dict[str, str] = {}
    for key in ASSESSMENT_QUESTION_FIELDS:
        val = data.get(key)
        if val is not None and isinstance(val, str) and val.strip():
            result[key] = val.strip()
        else:
            result[key] = str(val).strip() if val is not None else ""
    return result


def save_assessment_questions(
    db: Session,
    user_id: int,
    job_requirements_id: int,
    questions: dict[str, str],
) -> Assessments:
    """Create one Assessments row with the generated questions. Returns the created row."""
    row = Assessments(
        user_id=user_id,
        job_requirements_id=job_requirements_id,
        behavioral_question=questions.get("behavioral_question"),
        competency_based_question=questions.get("competency_based_question"),
        situational_question=questions.get("situational_question"),
        panel_question=questions.get("panel_question"),
        business_case_question=questions.get("business_case_question"),
        live_simulation_question=questions.get("live_simulation_question"),
        psychometric_question=questions.get("psychometric_question"),
        structured_reference_question=questions.get("structured_reference_question"),
        culture_alignment_question=questions.get("culture_alignment_question"),
        integrity_ethics_question=questions.get("integrity_ethics_question"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class GenerateAssessmentsRequest(BaseModel):
    job_requirements_id: int
    user_id: int


class AssessmentQuestionOut(BaseModel):
    id: int
    user_id: int
    job_requirements_id: int
    behavioral_question: str | None
    competency_based_question: str | None
    situational_question: str | None
    panel_question: str | None
    business_case_question: str | None
    live_simulation_question: str | None
    psychometric_question: str | None
    structured_reference_question: str | None
    culture_alignment_question: str | None
    integrity_ethics_question: str | None

    class Config:
        from_attributes = True


@router.post("/generate", response_model=AssessmentQuestionOut)
def generate_and_save_assessments(
    body: GenerateAssessmentsRequest,
    db: Session = Depends(get_db),
):
    """Load job requirements by id, generate 10 tailored questions with GPT-5.2, and save them to the assessments table."""
    job = db.get(JobRequirements, body.job_requirements_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job requirements not found")
    questions = generate_assessment_questions(job)
    assessment = save_assessment_questions(db, body.user_id, body.job_requirements_id, questions)
    # Index requirements + questions as embeddings so the conversation agent can retrieve context
    index_assessment(db, assessment, job)
    return AssessmentQuestionOut(
        id=assessment.id,
        user_id=assessment.user_id,
        job_requirements_id=assessment.job_requirements_id,
        behavioral_question=assessment.behavioral_question,
        competency_based_question=assessment.competency_based_question,
        situational_question=assessment.situational_question,
        panel_question=assessment.panel_question,
        business_case_question=assessment.business_case_question,
        live_simulation_question=assessment.live_simulation_question,
        psychometric_question=assessment.psychometric_question,
        structured_reference_question=assessment.structured_reference_question,
        culture_alignment_question=assessment.culture_alignment_question,
        integrity_ethics_question=assessment.integrity_ethics_question,
    )


class RetrieveContextRequest(BaseModel):
    """Request body for RAG context retrieval (used by the agent and for testing)."""
    assessment_id: int
    query: str
    limit: int = 15


class ContextChunkOut(BaseModel):
    content_type: str
    content: str
    assessment_id: int
    job_requirements_id: int


@router.post("/context", response_model=list[ContextChunkOut])
def retrieve_assessment_context(
    body: RetrieveContextRequest,
    db: Session = Depends(get_db),
):
    """Retrieve relevant requirements and questions for a query (for the conversation agent or testing)."""
    assessment = db.get(Assessments, body.assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    chunks = get_context_for_agent(
        db,
        body.query,
        assessment_id=body.assessment_id,
        limit=body.limit,
    )
    return [ContextChunkOut(**c) for c in chunks]
