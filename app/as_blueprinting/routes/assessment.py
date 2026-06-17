import asyncio
import os
import uuid

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.as_requirements.routes.ai_analysis import (
    analyze_job_description,
    build_job_description_from_linkedin_payload,
    insert_job_requirements,
)
from app.auth.login.deps import get_current_user_id
from app.db import get_db
from app.db.models import Analysis, AssessmentAccessLink, AssessmentCandidate, Assessments, JobRequirements, User
from app.routers.intelligence import build_status_response
from app.rag.embeddings import get_context_for_agent, index_assessment
from app.services.assessment_persistence import get_assessment_item_payloads, sync_assessment_items
from app.services.assessment_registry import (
    LEADERSHIP_ITEM_TEMPLATES,
    build_assessment_items,
    ensure_assessment_types_seeded,
    get_assessment_definition,
)
from app.services.assessment_candidates import (
    refresh_candidate_result_snapshots,
    serialize_candidate,
)
from app.services.cached_reads import invalidate_assessment_cache
from app.services.interview_links import (
    get_latest_assessment_access_link,
    issue_assessment_access_link,
    revoke_assessment_access_link,
    serialize_link_status,
)
from app.services.brightdata_linkedin import BrightDataError, fetch_linkedin_job_posting

router = APIRouter(prefix="/assessments", tags=["assessments"])
public_router = APIRouter(prefix="/assessments/public", tags=["assessments-public"])
ASSESSMENT_OVERVIEW_CODES = ("leadership_core", "mbti", "slii", "sii")
MBTI_PUBLIC_OWNER_USER_ID = int(os.getenv("MBTI_PUBLIC_OWNER_USER_ID", "1"))


class GenerateAssessmentsRequest(BaseModel):
    job_requirements_id: int | None = None
    user_id: int
    assessment_type_code: str = "leadership_core"
    issue_one_time_link: bool = False
    link_ttl_hours: int | None = None
    candidate_email: str | None = None
    issued_reason: str | None = None


class GenerateAssessmentFromLinkedInJobRequest(BaseModel):
    linkedin_job_url: str
    user_id: int
    assessment_type_code: str = "leadership_core"
    issue_one_time_link: bool = False
    link_ttl_hours: int | None = None
    candidate_email: str | None = None
    issued_reason: str | None = None


class InterviewLinkOut(BaseModel):
    id: int
    assessment_id: int
    status: str
    candidate_email: str | None = None
    max_uses: int
    use_count: int
    expires_at: str | None = None
    used_at: str | None = None
    revoked_at: str | None = None
    created_at: str | None = None
    interview_link: str | None = None


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
    latest_interview_link: InterviewLinkOut | None = None

    class Config:
        from_attributes = True


class IssueInterviewLinkRequest(BaseModel):
    candidate_email: str | None = None
    issued_reason: str | None = None
    ttl_hours: int | None = None


class PublicMbtiAssessmentRequest(BaseModel):
    candidate_email: str | None = None
    link_ttl_hours: int | None = None


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
    # Assessment updated — invalidate definition and items cache
    asyncio.run(invalidate_assessment_cache(str(row.id)))
    db.refresh(row)
    return row


def _assessment_requires_job_requirements(assessment_type_code: str) -> bool:
    return assessment_type_code == "leadership_core"


def _create_bootstrap_job_requirements(
    db: Session,
    *,
    assessment_type_code: str,
) -> JobRequirements:
    definition = get_assessment_definition(assessment_type_code)
    row = JobRequirements(
        job_id=f"{definition.code}-{uuid.uuid4()}",
        requirement=f"{definition.name}: {definition.description}",
        role_experience=definition.description,
    )
    db.add(row)
    db.flush()
    return row


def _resolve_job_requirements_for_generation(
    db: Session,
    *,
    assessment_type_code: str,
    job_requirements_id: int | None,
) -> tuple[JobRequirements | None, JobRequirements]:
    if job_requirements_id is not None:
        job = db.get(JobRequirements, job_requirements_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job requirements not found")
        return job, job

    if _assessment_requires_job_requirements(assessment_type_code):
        raise HTTPException(
            status_code=400,
            detail="job_requirements_id is required for leadership_core assessments.",
        )

    bootstrap_job = _create_bootstrap_job_requirements(
        db,
        assessment_type_code=assessment_type_code,
    )
    return None, bootstrap_job


def _serialize_generated_assessment(
    db: Session,
    assessment: Assessments,
    *,
    latest_link: AssessmentAccessLink | None = None,
    raw_token: str | None = None,
    base_url: str | None = None,
) -> AssessmentQuestionOut:
    items = [
        AssessmentItemOut(**item)
        for item in get_assessment_item_payloads(db, assessment)
    ]
    link_payload = serialize_link_status(
        latest_link,
        include_url=raw_token is not None,
        raw_token=raw_token,
        base_url=base_url,
    )
    return AssessmentQuestionOut(
        id=assessment.id,
        user_id=assessment.user_id,
        job_requirements_id=assessment.job_requirements_id,
        assessment_type_code=assessment.assessment_type_code,
        assessment_version=assessment.assessment_version,
        items=items,
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
        latest_interview_link=(
            InterviewLinkOut(**link_payload)
            if link_payload is not None
            else None
        ),
    )


def _require_owned_assessment(db: Session, assessment_id: int, current_user_id: int) -> Assessments:
    assessment = db.get(Assessments, assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if assessment.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="You can only access your own assessments.")
    return assessment


def _resolve_mbti_public_owner_user_id(db: Session) -> int:
    owner = db.get(User, MBTI_PUBLIC_OWNER_USER_ID)
    if not owner:
        raise HTTPException(
            status_code=503,
            detail=(
                "MBTI public creation is not configured. "
                f"Set MBTI_PUBLIC_OWNER_USER_ID to an existing user id."
            ),
        )
    return owner.id


def _generate_and_save_assessment(
    db: Session,
    *,
    request: Request,
    owner_user_id: int,
    assessment_type_code: str,
    job_requirements_id: int | None,
    issue_one_time_link: bool,
    created_by_user_id: int | None,
    candidate_email: str | None = None,
    issued_reason: str | None = None,
    link_ttl_hours: int | None = None,
) -> AssessmentQuestionOut:
    definition = get_assessment_definition(assessment_type_code)
    generation_job, persisted_job = _resolve_job_requirements_for_generation(
        db,
        assessment_type_code=definition.code,
        job_requirements_id=job_requirements_id,
    )
    items = build_assessment_items(definition, job=generation_job)
    assessment = save_assessment(
        db,
        user_id=owner_user_id,
        job_requirements_id=persisted_job.id,
        assessment_type_code=definition.code,
        assessment_version=definition.version,
        items=items,
    )
    latest_link = None
    raw_token = None
    if issue_one_time_link:
        latest_link, raw_token = issue_assessment_access_link(
            db,
            assessment_id=assessment.id,
            created_by_user_id=created_by_user_id,
            candidate_email=candidate_email,
            issued_reason=issued_reason,
            ttl_hours=link_ttl_hours,
        )
    return _serialize_generated_assessment(
        db,
        assessment,
        latest_link=latest_link,
        raw_token=raw_token,
        base_url=str(request.base_url).rstrip("/"),
    )


@public_router.post("/mbti", response_model=AssessmentQuestionOut)
def create_public_mbti_assessment(
    body: PublicMbtiAssessmentRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create an MBTI assessment without authentication and return a one-time interview link."""
    owner_user_id = _resolve_mbti_public_owner_user_id(db)
    return _generate_and_save_assessment(
        db,
        request=request,
        owner_user_id=owner_user_id,
        assessment_type_code="mbti",
        job_requirements_id=None,
        issue_one_time_link=True,
        created_by_user_id=owner_user_id,
        candidate_email=body.candidate_email,
        issued_reason="public_mbti",
        link_ttl_hours=body.link_ttl_hours,
    )


@router.post("/generate", response_model=AssessmentQuestionOut)
def generate_and_save_assessments(
    body: GenerateAssessmentsRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    """Generate an assessment instance from a reusable assessment type definition."""
    if body.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Assessments can only be generated for the authenticated user.")
    return _generate_and_save_assessment(
        db,
        request=request,
        owner_user_id=current_user_id,
        assessment_type_code=body.assessment_type_code,
        job_requirements_id=body.job_requirements_id,
        issue_one_time_link=body.issue_one_time_link,
        created_by_user_id=current_user_id,
        candidate_email=body.candidate_email,
        issued_reason=body.issued_reason,
        link_ttl_hours=body.link_ttl_hours,
    )


@router.post("/generate-from-linkedin-job", response_model=AssessmentQuestionOut)
def generate_assessment_from_linkedin_job(
    body: GenerateAssessmentFromLinkedInJobRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    """Fetch a LinkedIn job post via Bright Data and generate an assessment from it."""
    if body.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Assessments can only be generated for the authenticated user.")
    from app.tasks.assessment import dispatch_linkedin_assessment_chain

    task_id = dispatch_linkedin_assessment_chain(
        linkedin_url=body.linkedin_job_url,
        owner_user_id=str(current_user_id),
    )
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "task_id": task_id,
            "poll_url": f"/assessments/task-status/{task_id}",
        },
    )


@router.get("/task-status/{task_id}")
async def get_assessment_task_status(
    task_id: str,
    current_user_id: int = Depends(get_current_user_id),
):
    result = AsyncResult(task_id)
    return {
        "task_id": task_id,
        "state": result.state,
        "ready": result.ready(),
        "successful": result.successful() if result.ready() else None,
        "result": result.result if result.successful() else None,
    }


@router.post("/{assessment_id}/invite-link", response_model=InterviewLinkOut)
def issue_invite_link(
    assessment_id: int,
    body: IssueInterviewLinkRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    _require_owned_assessment(db, assessment_id, current_user_id)
    link, raw_token = issue_assessment_access_link(
        db,
        assessment_id=assessment_id,
        created_by_user_id=current_user_id,
        candidate_email=body.candidate_email,
        issued_reason=body.issued_reason,
        ttl_hours=body.ttl_hours,
    )
    payload = serialize_link_status(
        link,
        include_url=True,
        raw_token=raw_token,
        base_url=str(request.base_url).rstrip("/"),
    )
    return InterviewLinkOut(**payload)


@router.post("/{assessment_id}/invite-link/revoke", response_model=InterviewLinkOut)
def revoke_invite_link(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    _require_owned_assessment(db, assessment_id, current_user_id)
    link = get_latest_assessment_access_link(db, assessment_id)
    if not link:
        raise HTTPException(status_code=404, detail="No interview link found for this assessment.")
    payload = serialize_link_status(revoke_assessment_access_link(db, link))
    return InterviewLinkOut(**payload)


@router.get("/{assessment_id}/invite-link/status", response_model=InterviewLinkOut)
def get_invite_link_status(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    _require_owned_assessment(db, assessment_id, current_user_id)
    link = get_latest_assessment_access_link(db, assessment_id)
    if not link:
        raise HTTPException(status_code=404, detail="No interview link found for this assessment.")
    payload = serialize_link_status(link)
    return InterviewLinkOut(**payload)


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


class JobRequirementsOut(BaseModel):
    id: int
    job_id: str
    requirement: str
    skill: str | None = None
    soft_skill: str | None = None
    experience: str | None = None
    education: str | None = None
    certification: str | None = None
    responsibility: str | None = None
    language: str | None = None
    industry_experience: str | None = None
    role_experience: str | None = None
    location: str | None = None
    availability: str | None = None
    work_authorization: str | None = None
    seniority_level: str | None = None
    culture_fit: str | None = None


class AnalysisOut(BaseModel):
    id: int
    analysis: str
    aggregated_traits: dict | list | None = None
    consistency_scores: dict | list | None = None
    trait_gaps: dict | list | None = None
    contradictions: dict | list | None = None
    behavioral_patterns: dict | list | None = None


class AssessmentOverviewItemOut(BaseModel):
    id: int
    user_id: int
    job_requirements_id: int
    assessment_type_code: str
    assessment_version: str
    job_requirements: JobRequirementsOut | None = None
    analysis: AnalysisOut | None = None
    latest_interview_link: InterviewLinkOut | None = None


class AssessmentCandidateOut(BaseModel):
    id: int
    assessment_id: int
    access_link_id: int | None = None
    first_name: str
    last_name: str
    email: str
    assessment_type_code: str
    analysis: str | None = None
    prediction: str | None = None
    fit_score: float | None = None
    confidence_score: float | None = None
    risk_flags: dict | list | None = None
    link_token: str
    link_created_at: str | None = None
    link_expires_at: str | None = None
    last_result_sync_at: str | None = None
    completed_question_count: int | None = None
    total_questions: int | None = None
    interview_complete: bool | None = None
    analysis_status: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class UserAssessmentsOverviewOut(BaseModel):
    technical_assessments: list[AssessmentOverviewItemOut]
    mbti_assessments: list[AssessmentOverviewItemOut]
    sii_assessments: list[AssessmentOverviewItemOut]


@router.get("/user/{user_id}/overview", response_model=UserAssessmentsOverviewOut)
def get_user_assessments_overview(
    user_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    """Return technical/MBTI/SLII assessments for a user with job requirements and latest analysis."""
    if user_id != current_user_id:
        raise HTTPException(status_code=403, detail="You can only access your own assessments.")

    assessments = (
        db.query(Assessments)
        .filter(
            Assessments.user_id == user_id,
            Assessments.assessment_type_code.in_(ASSESSMENT_OVERVIEW_CODES),
        )
        .order_by(Assessments.id.desc())
        .all()
    )

    job_requirements_by_id = {}
    if assessments:
        job_requirement_ids = {a.job_requirements_id for a in assessments}
        job_rows = db.query(JobRequirements).filter(JobRequirements.id.in_(job_requirement_ids)).all()
        job_requirements_by_id = {job.id: job for job in job_rows}

    latest_analysis_by_assessment_id: dict[int, Analysis] = {}
    if assessments:
        assessment_ids = [a.id for a in assessments]
        analysis_rows = (
            db.query(Analysis)
            .filter(Analysis.assessment_id.in_(assessment_ids))
            .order_by(Analysis.assessment_id, Analysis.id.desc())
            .all()
        )
        for row in analysis_rows:
            if row.assessment_id not in latest_analysis_by_assessment_id:
                latest_analysis_by_assessment_id[row.assessment_id] = row

    latest_link_by_assessment_id: dict[int, AssessmentAccessLink] = {}
    if assessments:
        assessment_ids = [a.id for a in assessments]
        link_rows = (
            db.query(AssessmentAccessLink)
            .filter(AssessmentAccessLink.assessment_id.in_(assessment_ids))
            .order_by(AssessmentAccessLink.assessment_id, AssessmentAccessLink.id.desc())
            .all()
        )
        for row in link_rows:
            if row.assessment_id not in latest_link_by_assessment_id:
                latest_link_by_assessment_id[row.assessment_id] = row

    response = UserAssessmentsOverviewOut(
        technical_assessments=[],
        mbti_assessments=[],
        sii_assessments=[],
    )

    for assessment in assessments:
        target_list = response.technical_assessments
        if assessment.assessment_type_code == "mbti":
            target_list = response.mbti_assessments
        elif assessment.assessment_type_code in {"slii", "sii"}:
            target_list = response.sii_assessments

        job_row = job_requirements_by_id.get(assessment.job_requirements_id)
        analysis_row = latest_analysis_by_assessment_id.get(assessment.id)
        latest_link = latest_link_by_assessment_id.get(assessment.id)

        target_list.append(
            AssessmentOverviewItemOut(
                id=assessment.id,
                user_id=assessment.user_id,
                job_requirements_id=assessment.job_requirements_id,
                assessment_type_code=assessment.assessment_type_code,
                assessment_version=assessment.assessment_version,
                job_requirements=(
                    JobRequirementsOut(
                        id=job_row.id,
                        job_id=job_row.job_id,
                        requirement=job_row.requirement,
                        skill=job_row.skill,
                        soft_skill=job_row.soft_skill,
                        experience=job_row.experience,
                        education=job_row.education,
                        certification=job_row.certification,
                        responsibility=job_row.responsibility,
                        language=job_row.language,
                        industry_experience=job_row.industry_experience,
                        role_experience=job_row.role_experience,
                        location=job_row.location,
                        availability=job_row.availability,
                        work_authorization=job_row.work_authorization,
                        seniority_level=job_row.seniority_level,
                        culture_fit=job_row.culture_fit,
                    )
                    if job_row
                    else None
                ),
                analysis=(
                    AnalysisOut(
                        id=analysis_row.id,
                        analysis=analysis_row.analysis,
                        aggregated_traits=analysis_row.aggregated_traits,
                        consistency_scores=analysis_row.consistency_scores,
                        trait_gaps=analysis_row.trait_gaps,
                        contradictions=analysis_row.contradictions,
                        behavioral_patterns=analysis_row.behavioral_patterns,
                    )
                    if analysis_row
                    else None
                ),
                latest_interview_link=(
                    InterviewLinkOut(**serialize_link_status(latest_link))
                    if latest_link
                    else None
                ),
            )
        )

    return response


@router.get("/candidates", response_model=list[AssessmentCandidateOut])
def list_assessment_candidates(
    assessment_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    query = (
        db.query(AssessmentCandidate)
        .join(Assessments, Assessments.id == AssessmentCandidate.assessment_id)
        .filter(Assessments.user_id == current_user_id)
        .order_by(AssessmentCandidate.created_at.desc(), AssessmentCandidate.id.desc())
    )
    if assessment_id is not None:
        query = query.filter(AssessmentCandidate.assessment_id == assessment_id)

    candidates = query.all()
    candidate_status_by_id: dict[int, dict] = {}
    for candidate in candidates:
        candidate_status_by_id[candidate.id] = build_status_response(
            db,
            candidate.assessment_id,
            candidate_id=candidate.id,
        )
    refresh_candidate_result_snapshots(db, candidates)
    return [
        AssessmentCandidateOut(
            **serialize_candidate(candidate),
            completed_question_count=candidate_status_by_id[candidate.id].get("completed_question_count"),
            total_questions=candidate_status_by_id[candidate.id].get("total_questions"),
            interview_complete=candidate_status_by_id[candidate.id].get("interview_complete"),
            analysis_status=candidate_status_by_id[candidate.id].get("status"),
        )
        for candidate in candidates
    ]
