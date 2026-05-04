from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import (
    Analysis,
    AssessmentAccessLink,
    AssessmentCandidate,
    AssessmentResult,
    Assessments,
    Predictions,
)
from app.services.interview_links import (
    build_candidate_fingerprint,
    consume_assessment_access_link,
    peek_assessment_access_link,
    utcnow,
)


@dataclass(frozen=True)
class CandidateRegistrationContext:
    assessment_id: int
    assessment_type_code: str
    candidate_email: str | None


def get_candidate_registration_context(db: Session, raw_token: str) -> CandidateRegistrationContext:
    link = peek_assessment_access_link(db, raw_token)
    assessment = db.get(Assessments, link.assessment_id)
    if not assessment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found.")
    return CandidateRegistrationContext(
        assessment_id=assessment.id,
        assessment_type_code=assessment.assessment_type_code,
        candidate_email=link.candidate_email,
    )


def register_candidate_for_link(
    db: Session,
    *,
    raw_token: str,
    first_name: str,
    last_name: str,
    email: str,
    client_ip: str | None,
    user_agent: str | None,
) -> tuple[AssessmentCandidate, AssessmentAccessLink]:
    normalized_first_name = _require_non_empty(first_name, "First name")
    normalized_last_name = _require_non_empty(last_name, "Last name")
    normalized_email = _normalize_email(email)
    fingerprint = build_candidate_fingerprint(client_ip, user_agent)
    link = consume_assessment_access_link(db, raw_token, fingerprint=fingerprint, commit=False)
    assessment = db.get(Assessments, link.assessment_id)
    if not assessment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found.")

    candidate = AssessmentCandidate(
        assessment_id=assessment.id,
        access_link_id=link.id,
        first_name=normalized_first_name,
        last_name=normalized_last_name,
        email=normalized_email,
        assessment_type_code=assessment.assessment_type_code,
        link_token=raw_token,
        link_created_at=link.created_at,
        link_expires_at=link.expires_at,
    )

    try:
        link.candidate_email = normalized_email
        link.updated_at = utcnow()
        db.add(link)
        db.add(candidate)
        db.commit()
        db.refresh(link)
        db.refresh(candidate)
        return candidate, link
    except Exception:
        db.rollback()
        raise


def refresh_candidate_result_snapshots(
    db: Session,
    candidates: Iterable[AssessmentCandidate],
) -> list[AssessmentCandidate]:
    candidate_list = list(candidates)
    if not candidate_list:
        return candidate_list

    candidate_ids = [candidate.id for candidate in candidate_list]
    analyses = (
        db.query(Analysis)
        .filter(Analysis.candidate_id.in_(candidate_ids))
        .order_by(Analysis.candidate_id, Analysis.id.desc())
        .all()
    )
    latest_analysis_by_candidate_id: dict[int, Analysis] = {}
    for row in analyses:
        if row.candidate_id is not None and row.candidate_id not in latest_analysis_by_candidate_id:
            latest_analysis_by_candidate_id[row.candidate_id] = row

    predictions_by_analysis_id: dict[int, Predictions] = {}
    if latest_analysis_by_candidate_id:
        prediction_rows = (
            db.query(Predictions)
            .filter(Predictions.analysis_id.in_([row.id for row in latest_analysis_by_candidate_id.values()]))
            .order_by(Predictions.analysis_id, Predictions.id.desc())
            .all()
        )
        for row in prediction_rows:
            if row.analysis_id not in predictions_by_analysis_id:
                predictions_by_analysis_id[row.analysis_id] = row

    result_rows = (
        db.query(AssessmentResult)
        .filter(AssessmentResult.candidate_id.in_(candidate_ids))
        .all()
    )
    results_by_candidate_id = {row.candidate_id: row for row in result_rows if row.candidate_id is not None}

    changed = False
    for candidate in candidate_list:
        analysis = latest_analysis_by_candidate_id.get(candidate.id)
        prediction = predictions_by_analysis_id.get(analysis.id) if analysis else None
        result = results_by_candidate_id.get(candidate.id)
        changed = _apply_result_snapshot(candidate, analysis=analysis, prediction=prediction, result=result) or changed

    if changed:
        db.commit()
        for candidate in candidate_list:
            db.refresh(candidate)
    return candidate_list


def serialize_candidate(candidate: AssessmentCandidate) -> dict:
    return {
        "id": candidate.id,
        "assessment_id": candidate.assessment_id,
        "access_link_id": candidate.access_link_id,
        "first_name": candidate.first_name,
        "last_name": candidate.last_name,
        "email": candidate.email,
        "assessment_type_code": candidate.assessment_type_code,
        "analysis": candidate.analysis_snapshot,
        "prediction": candidate.prediction_snapshot,
        "fit_score": candidate.fit_score,
        "confidence_score": candidate.confidence_score,
        "risk_flags": candidate.risk_flags,
        "link_token": candidate.link_token,
        "link_created_at": candidate.link_created_at.isoformat() if candidate.link_created_at else None,
        "link_expires_at": candidate.link_expires_at.isoformat() if candidate.link_expires_at else None,
        "last_result_sync_at": candidate.last_result_sync_at.isoformat() if candidate.last_result_sync_at else None,
        "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
        "updated_at": candidate.updated_at.isoformat() if candidate.updated_at else None,
    }


def _apply_result_snapshot(
    candidate: AssessmentCandidate,
    *,
    analysis: Analysis | None,
    prediction: Predictions | None,
    result: AssessmentResult | None,
) -> bool:
    next_analysis = analysis.analysis if analysis else None
    next_prediction = prediction.prediction if prediction else None
    next_fit_score = result.fit_score if result and result.fit_score is not None else (prediction.fit_score if prediction else None)
    next_confidence = (
        result.confidence_score
        if result and result.confidence_score is not None
        else (prediction.confidence_score if prediction else None)
    )
    next_risk_flags = result.risk_flags if result and result.risk_flags is not None else (prediction.risk_flags if prediction else None)

    changed = False
    if candidate.analysis_snapshot != next_analysis:
        candidate.analysis_snapshot = next_analysis
        changed = True
    if candidate.prediction_snapshot != next_prediction:
        candidate.prediction_snapshot = next_prediction
        changed = True
    if candidate.fit_score != next_fit_score:
        candidate.fit_score = next_fit_score
        changed = True
    if candidate.confidence_score != next_confidence:
        candidate.confidence_score = next_confidence
        changed = True
    if candidate.risk_flags != next_risk_flags:
        candidate.risk_flags = next_risk_flags
        changed = True
    if changed:
        candidate.last_result_sync_at = utcnow()
    return changed


def _require_non_empty(value: str, field_name: str) -> str:
    normalized = (value or "").strip()
    if normalized:
        return normalized
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} is required.")


def _normalize_email(value: str) -> str:
    normalized = _require_non_empty(value, "Email").lower()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A valid email is required.")
    return normalized
