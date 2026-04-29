from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.candidate_access import (
    CANDIDATE_ACCESS_COOKIE_NAME,
    CANDIDATE_SESSION_EXPIRE_MINUTES,
    CandidateAccessContext,
    create_candidate_session_token,
    require_candidate_assessment_access,
)
from app.db import get_db
from app.db.models import AssessmentCandidate, Assessments
from app.routers.intelligence import (
    build_analysis_response,
    build_predictions_response,
    build_status_response,
)
from app.services.assessment_candidates import (
    get_candidate_registration_context,
    refresh_candidate_result_snapshots,
    register_candidate_for_link,
)
from app.services.interview_links import (
    enforce_link_open_rate_limit,
)

router = APIRouter(prefix="/candidate", tags=["candidate"])


class CandidateInterviewAccessOut(BaseModel):
    assessment_id: int
    assessment_type_code: str
    candidate_email: str | None = None
    registration_endpoint: str
    session_endpoint: str


class BeginCandidateInterviewRequest(BaseModel):
    first_name: str
    last_name: str
    email: str


class BeginCandidateInterviewOut(BaseModel):
    candidate_id: int
    assessment_id: int
    candidate_token: str
    expires_in_seconds: int
    session_endpoint: str
    redirect_path: str


class CandidateInterviewSessionOut(BaseModel):
    assessment_id: int
    assessment_type_code: str
    candidate: dict | None = None
    status_endpoint: str
    analysis_endpoint: str
    predictions_endpoint: str
    websocket_path: str


@router.get("/interview/session", response_model=CandidateInterviewSessionOut)
def get_candidate_interview_session(
    assessment_id: int,
    context: CandidateAccessContext = Depends(require_candidate_assessment_access),
    db: Session = Depends(get_db),
):
    assessment = db.get(Assessments, assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")
    candidate = (
        db.query(AssessmentCandidate)
        .filter(AssessmentCandidate.access_link_id == context.link_id)
        .first()
    )
    return CandidateInterviewSessionOut(
        assessment_id=assessment.id,
        assessment_type_code=assessment.assessment_type_code,
        candidate=(
            {
                "id": candidate.id,
                "first_name": candidate.first_name,
                "last_name": candidate.last_name,
                "email": candidate.email,
            }
            if candidate
            else None
        ),
        status_endpoint=f"/candidate/assessment/{assessment.id}/status",
        analysis_endpoint=f"/candidate/assessment/{assessment.id}/analysis",
        predictions_endpoint=f"/candidate/assessment/{assessment.id}/predictions",
        websocket_path=f"/agent/ws?assessment_id={assessment.id}",
    )


@router.get("/interview/{raw_token}", response_model=CandidateInterviewAccessOut)
def open_candidate_interview(raw_token: str, request: Request, db: Session = Depends(get_db)):
    client_ip = getattr(getattr(request, "client", None), "host", None)
    enforce_link_open_rate_limit(client_ip)
    context = get_candidate_registration_context(db, raw_token)
    return CandidateInterviewAccessOut(
        assessment_id=context.assessment_id,
        assessment_type_code=context.assessment_type_code,
        candidate_email=context.candidate_email,
        registration_endpoint=f"/candidate/interview/{raw_token}/begin",
        session_endpoint=f"/candidate/interview/session?assessment_id={context.assessment_id}",
    )


@router.post("/interview/{raw_token}/begin", response_model=BeginCandidateInterviewOut)
def begin_candidate_interview(
    raw_token: str,
    request: Request,
    body: BeginCandidateInterviewRequest,
    db: Session = Depends(get_db),
):
    client_ip = getattr(getattr(request, "client", None), "host", None)
    user_agent = request.headers.get("user-agent")
    candidate, link = register_candidate_for_link(
        db,
        raw_token=raw_token,
        first_name=body.first_name,
        last_name=body.last_name,
        email=body.email,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    session_token = create_candidate_session_token(
        assessment_id=link.assessment_id,
        link_id=link.id,
    )
    payload = BeginCandidateInterviewOut(
        candidate_id=candidate.id,
        assessment_id=candidate.assessment_id,
        candidate_token=session_token,
        expires_in_seconds=CANDIDATE_SESSION_EXPIRE_MINUTES * 60,
        session_endpoint=f"/candidate/interview/session?assessment_id={candidate.assessment_id}",
        redirect_path=f"/candidate/interview/session?assessment_id={candidate.assessment_id}",
    )
    response = JSONResponse(content=payload.model_dump())
    response.set_cookie(
        key=CANDIDATE_ACCESS_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=CANDIDATE_SESSION_EXPIRE_MINUTES * 60,
    )
    return response


@router.get("/assessment/{assessment_id}/status")
def get_candidate_status(
    assessment_id: int,
    _: object = Depends(require_candidate_assessment_access),
    db: Session = Depends(get_db),
):
    return build_status_response(db, assessment_id)


@router.get("/assessment/{assessment_id}/analysis")
def get_candidate_analysis(
    assessment_id: int,
    context: CandidateAccessContext = Depends(require_candidate_assessment_access),
    db: Session = Depends(get_db),
):
    candidates = (
        db.query(AssessmentCandidate)
        .filter(AssessmentCandidate.access_link_id == context.link_id)
        .all()
    )
    refresh_candidate_result_snapshots(db, candidates)
    return build_analysis_response(db, assessment_id)


@router.get("/assessment/{assessment_id}/predictions")
def get_candidate_predictions(
    assessment_id: int,
    context: CandidateAccessContext = Depends(require_candidate_assessment_access),
    db: Session = Depends(get_db),
):
    candidates = (
        db.query(AssessmentCandidate)
        .filter(AssessmentCandidate.access_link_id == context.link_id)
        .all()
    )
    refresh_candidate_result_snapshots(db, candidates)
    return build_predictions_response(db, assessment_id)
