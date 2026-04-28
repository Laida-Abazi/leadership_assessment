from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.candidate_access import (
    CANDIDATE_ACCESS_COOKIE_NAME,
    CANDIDATE_SESSION_EXPIRE_MINUTES,
    create_candidate_session_token,
    require_candidate_assessment_access,
)
from app.db import get_db
from app.db.models import Assessments
from app.routers.intelligence import (
    build_analysis_response,
    build_predictions_response,
    build_status_response,
)
from app.services.interview_links import (
    build_candidate_fingerprint,
    consume_assessment_access_link,
    enforce_link_open_rate_limit,
)

router = APIRouter(prefix="/candidate", tags=["candidate"])


@router.get("/interview/session", response_class=FileResponse)
def serve_candidate_interview_page(
    assessment_id: int,
    _: object = Depends(require_candidate_assessment_access),
    db: Session = Depends(get_db),
):
    assessment = db.get(Assessments, assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")
    path = Path(__file__).resolve().parent.parent / "templates" / "candidate_interview.html"
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return FileResponse(path)


@router.get("/interview/{raw_token}")
def open_candidate_interview(raw_token: str, request: Request, db: Session = Depends(get_db)):
    client_ip = getattr(getattr(request, "client", None), "host", None)
    user_agent = request.headers.get("user-agent")
    enforce_link_open_rate_limit(client_ip)
    fingerprint = build_candidate_fingerprint(client_ip, user_agent)
    link = consume_assessment_access_link(db, raw_token, fingerprint=fingerprint)
    session_token = create_candidate_session_token(
        assessment_id=link.assessment_id,
        link_id=link.id,
    )

    redirect = RedirectResponse(
        url=f"/candidate/interview/session?assessment_id={link.assessment_id}",
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
    _: object = Depends(require_candidate_assessment_access),
    db: Session = Depends(get_db),
):
    return build_analysis_response(db, assessment_id)


@router.get("/assessment/{assessment_id}/predictions")
def get_candidate_predictions(
    assessment_id: int,
    _: object = Depends(require_candidate_assessment_access),
    db: Session = Depends(get_db),
):
    return build_predictions_response(db, assessment_id)
