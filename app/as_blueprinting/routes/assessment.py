from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.db.models import Assessments, JobRequirements
from app.rag.embeddings import get_context_for_agent, index_assessment
from app.services.assessment_persistence import get_assessment_item_payloads, sync_assessment_items
from app.services.assessment_registry import (
    LEADERSHIP_ITEM_TEMPLATES,
    build_assessment_items,
    ensure_assessment_types_seeded,
    get_assessment_definition,
)

router = APIRouter(prefix="/assessments", tags=["assessments"])


class GenerateAssessmentsRequest(BaseModel):
    job_requirements_id: int
    user_id: int
    assessment_type_code: str = "leadership_core"


class AssessmentItemOut(BaseModel):
    item_key: str
    display_label: str
    item_order: int
    item_kind: str
    prompt_text: str
    item_meta: dict | None = None


class AssessmentQuestionOut(BaseModel):
    id: int
    user_id: int
    job_requirements_id: int
    assessment_type_code: str
    assessment_version: str
    items: list[AssessmentItemOut]
    behavioral_question: str | None = None
    competency_based_question: str | None = None
    situational_question: str | None = None
    panel_question: str | None = None
    business_case_question: str | None = None
    live_simulation_question: str | None = None
    psychometric_question: str | None = None
    structured_reference_question: str | None = None
    culture_alignment_question: str | None = None
    integrity_ethics_question: str | None = None

    class Config:
        from_attributes = True


def save_assessment(
    db: Session,
    *,
    user_id: int,
    job_requirements_id: int,
    assessment_type_code: str,
    assessment_version: str,
    items: list[dict],
) -> Assessments:
    row = Assessments(
        user_id=user_id,
        job_requirements_id=job_requirements_id,
        assessment_type_code=assessment_type_code,
        assessment_version=assessment_version,
    )
    if assessment_type_code == "leadership_core":
        for template in LEADERSHIP_ITEM_TEMPLATES:
            setattr(
                row,
                template.key,
                next((item["prompt_text"] for item in items if item["item_key"] == template.key), None),
            )
    db.add(row)
    db.flush()
    sync_assessment_items(db, row, items)
    db.commit()
    db.refresh(row)
    return row


@router.post("/generate", response_model=AssessmentQuestionOut)
def generate_and_save_assessments(
    body: GenerateAssessmentsRequest,
    db: Session = Depends(get_db),
):
    """Generate an assessment instance from a reusable assessment type definition."""
    job = db.get(JobRequirements, body.job_requirements_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job requirements not found")
    ensure_assessment_types_seeded(db)
    definition = get_assessment_definition(body.assessment_type_code)
    items = build_assessment_items(definition, job=job)
    assessment = save_assessment(
        db,
        user_id=body.user_id,
        job_requirements_id=body.job_requirements_id,
        assessment_type_code=definition.code,
        assessment_version=definition.version,
        items=items,
    )
    index_assessment(db, assessment, job)
    item_payloads = get_assessment_item_payloads(db, assessment)
    response_payload = {
        "id": assessment.id,
        "user_id": assessment.user_id,
        "job_requirements_id": assessment.job_requirements_id,
        "assessment_type_code": assessment.assessment_type_code,
        "assessment_version": assessment.assessment_version,
        "items": item_payloads,
    }
    if assessment.assessment_type_code == "leadership_core":
        for template in LEADERSHIP_ITEM_TEMPLATES:
            response_payload[template.key] = getattr(assessment, template.key, None)
    return AssessmentQuestionOut(**response_payload)


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
