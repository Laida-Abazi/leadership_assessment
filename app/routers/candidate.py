from __future__ import annotations

import html
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
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


@router.get("/interview/session", response_class=FileResponse)
def serve_candidate_interview_page(
    assessment_id: int,
    _: CandidateAccessContext = Depends(require_candidate_assessment_access),
    db: Session = Depends(get_db),
):
    assessment = db.get(Assessments, assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")
    path = Path(__file__).resolve().parent.parent / "templates" / "candidate_interview.html"
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return FileResponse(path)


@router.get("/interview/{raw_token}", response_class=HTMLResponse)
def open_candidate_interview(raw_token: str, request: Request, db: Session = Depends(get_db)):
    client_ip = getattr(getattr(request, "client", None), "host", None)
    enforce_link_open_rate_limit(client_ip)
    context = get_candidate_registration_context(db, raw_token)
    path = Path(__file__).resolve().parent.parent / "templates" / "candidate_identity_form.html"
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")

    content = path.read_text(encoding="utf-8")
    content = content.replace("__FORM_ACTION__", html.escape(f"/candidate/interview/{raw_token}/begin", quote=True))
    content = content.replace("__ASSESSMENT_TYPE__", html.escape(context.assessment_type_code))
    content = content.replace("__PREFILLED_EMAIL__", html.escape(context.candidate_email or "", quote=True))
    return HTMLResponse(content, headers={"Cache-Control": "no-store"})


@router.post("/interview/{raw_token}/begin")
def begin_candidate_interview(
    raw_token: str,
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    client_ip = getattr(getattr(request, "client", None), "host", None)
    user_agent = request.headers.get("user-agent")
    candidate, link = register_candidate_for_link(
        db,
        raw_token=raw_token,
        first_name=first_name,
        last_name=last_name,
        email=email,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    session_token = create_candidate_session_token(
        assessment_id=link.assessment_id,
        link_id=link.id,
    )
    redirect = RedirectResponse(
        url=f"/candidate/interview/session?assessment_id={candidate.assessment_id}",
        status_code=303,
    )
    redirect.set_cookie(
        key=CANDIDATE_ACCESS_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=CANDIDATE_SESSION_EXPIRE_MINUTES * 60,
    )
    return redirect


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
