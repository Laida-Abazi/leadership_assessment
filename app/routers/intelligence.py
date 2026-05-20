"""
Intelligence REST endpoints — expose response segments, signals, analysis,
and predictions produced by the intelligence pipeline.
"""
from __future__ import annotations

import logging
from typing import Any

from celery.result import AsyncResult
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.db.models import Analysis, AssessmentResult, Assessments, Predictions
from app.db.models.response_segment import ResponseSegment
from app.db.models.response_signal import ResponseSignal
from app.services.assessment_persistence import count_saved_answers, get_assessment_item_payloads
from app.services.task_registry import task_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


def _apply_candidate_scope(query, model, candidate_id: int | None):
    if candidate_id is None:
        return query.filter(model.candidate_id.is_(None))
    return query.filter(model.candidate_id == candidate_id)


# ---------------------------------------------------------------------------
# GET /intelligence/assessment/{assessment_id}/segments
# ---------------------------------------------------------------------------

@router.get("/assessment/{assessment_id}/segments")
def get_segments(
    assessment_id: int,
    candidate_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return all response_segments for this assessment ordered by sequence_order."""
    _require_assessment(db, assessment_id)
    rows = (
        _apply_candidate_scope(
            db.query(ResponseSegment).filter(ResponseSegment.assessment_id == assessment_id),
            ResponseSegment,
            candidate_id,
        )
        .order_by(ResponseSegment.sequence_order)
        .all()
    )
    return [
        {
            "id": r.id,
            "assessment_id": r.assessment_id,
            "response_type": r.response_type,
            "question_id": r.question_id,
            "segment_text": r.segment_text,
            "sequence_order": r.sequence_order,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /intelligence/assessment/{assessment_id}/signals
# ---------------------------------------------------------------------------

@router.get("/assessment/{assessment_id}/signals")
def get_signals(
    assessment_id: int,
    candidate_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, list[dict]]:
    """Return response_signals grouped by response_type."""
    _require_assessment(db, assessment_id)
    rows = (
        _apply_candidate_scope(
            db.query(ResponseSignal).filter(ResponseSignal.assessment_id == assessment_id),
            ResponseSignal,
            candidate_id,
        )
        .order_by(ResponseSignal.id)
        .all()
    )
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        entry = {
            "id": r.id,
            "response_segment_id": r.response_segment_id,
            "traits": r.traits,
            "strengths": r.strengths,
            "risks": r.risks,
            "confidence": r.confidence,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        grouped.setdefault(r.response_type, []).append(entry)
    return grouped


# ---------------------------------------------------------------------------
# GET /intelligence/assessment/{assessment_id}/analysis
# ---------------------------------------------------------------------------

@router.get("/assessment/{assessment_id}/analysis")
def get_analysis(
    assessment_id: int,
    candidate_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return the full Analysis row including all JSONB intelligence fields."""
    return build_analysis_response(db, assessment_id, candidate_id=candidate_id)


# ---------------------------------------------------------------------------
# GET /intelligence/assessment/{assessment_id}/predictions
# ---------------------------------------------------------------------------

@router.get("/assessment/{assessment_id}/predictions")
def get_predictions(
    assessment_id: int,
    candidate_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return the Predictions row including fit_score, risk_flags, and hiring_recommendation."""
    return build_predictions_response(db, assessment_id, candidate_id=candidate_id)


@router.get("/assessment/{assessment_id}/status")
def get_intelligence_processing_status(
    assessment_id: int,
    candidate_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return whether signal extraction / analysis is still running."""
    return build_status_response(db, assessment_id, candidate_id=candidate_id)


# ---------------------------------------------------------------------------
# POST /intelligence/assessment/{assessment_id}/rerun
# ---------------------------------------------------------------------------

@router.post("/assessment/{assessment_id}/rerun", status_code=202)
async def rerun_analysis(
    assessment_id: int,
    background_tasks: BackgroundTasks,
    candidate_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Manually trigger final analysis using already collected segments/signals."""
    _require_assessment(db, assessment_id)
    from app.tasks.analysis import dispatch_analysis_chord

    if task_registry.is_analysis_running(candidate_id):
        return {
            "status": "already_running",
            "message": "Analysis is already in progress",
        }

    segment_ids = get_segment_ids_for_candidate(assessment_id, candidate_id=candidate_id, db=db)
    if not segment_ids:
        return {
            "status": "error",
            "message": "No segments found. Interview may be incomplete.",
        }

    pipeline_id = dispatch_analysis_chord(candidate_id, assessment_id, segment_ids)
    logger.info(
        "[intelligence] rerun_analysis dispatched pipeline_id=%s assessment_id=%s candidate_id=%s segments=%s",
        pipeline_id,
        assessment_id,
        candidate_id,
        len(segment_ids),
    )
    background_tasks.add_task(lambda: None)
    return {
        "status": "dispatched",
        "pipeline_id": pipeline_id,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_assessment(db: Session, assessment_id: int) -> Assessments:
    assessment = db.get(Assessments, assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail=f"Assessment {assessment_id} not found.")
    return assessment


def build_status_response(
    db: Session,
    assessment_id: int,
    *,
    candidate_id: int | None = None,
) -> dict[str, Any]:
    # READ-ONLY. This endpoint never triggers computation. See POST /rerun.
    assessment = _require_assessment(db, assessment_id)
    ordered_item_keys = [
        item["item_key"]
        for item in get_assessment_item_payloads(db, assessment)
        if item.get("prompt_text")
    ]
    completed_question_count = count_saved_answers(
        assessment_id,
        ordered_item_keys,
        candidate_id=candidate_id,
    )
    total_questions = len(ordered_item_keys)
    interview_complete = total_questions > 0 and completed_question_count >= total_questions

    pipeline_id = task_registry.get_pipeline_id(candidate_id)
    if pipeline_id:
        result = AsyncResult(pipeline_id)
        if result.state == "SUCCESS":
            status: dict[str, Any] = {"status": "complete", "source": "pipeline"}
        elif result.state == "FAILURE":
            status = {
                "status": "failed",
                "source": "pipeline",
                "error": str(result.result),
            }
        else:
            status = {
                "status": "processing",
                "state": result.state,
                "source": "pipeline",
            }
        status["pipeline_id"] = pipeline_id
    else:
        analysis = get_analysis_by_candidate(assessment_id, candidate_id=candidate_id, db=db)
        if analysis:
            status = {"status": "complete", "source": "database"}
        else:
            status = {
                "status": "pending",
                "message": "Use POST /rerun to trigger analysis",
            }

    status["assessment_id"] = assessment_id
    status["candidate_id"] = candidate_id
    status["completed_question_count"] = completed_question_count
    status["total_questions"] = total_questions
    status["interview_complete"] = interview_complete
    return status


def get_analysis_by_candidate(
    assessment_id: int,
    *,
    candidate_id: int | None = None,
    db: Session,
) -> Analysis | None:
    return _apply_candidate_scope(
        db.query(Analysis).filter(Analysis.assessment_id == assessment_id),
        Analysis,
        candidate_id,
    ).order_by(Analysis.id.desc()).first()


def get_segment_ids_for_candidate(
    assessment_id: int,
    *,
    candidate_id: int | None = None,
    db: Session,
) -> list[str]:
    rows = (
        _apply_candidate_scope(
            db.query(ResponseSegment.id).filter(ResponseSegment.assessment_id == assessment_id),
            ResponseSegment,
            candidate_id,
        )
        .order_by(ResponseSegment.id.asc())
        .all()
    )
    return [str(segment_id) for (segment_id,) in rows]


def build_analysis_response(
    db: Session,
    assessment_id: int,
    *,
    candidate_id: int | None = None,
) -> dict[str, Any]:
    _require_assessment(db, assessment_id)
    row = _apply_candidate_scope(
        db.query(Analysis).filter(Analysis.assessment_id == assessment_id),
        Analysis,
        candidate_id,
    ).order_by(Analysis.id.desc()).first()
    if not row:
        raise HTTPException(status_code=404, detail="Analysis not found for this assessment.")
    return {
        "id": row.id,
        "assessment_id": row.assessment_id,
        "candidate_id": row.candidate_id,
        "job_requirements_id": row.job_requirements_id,
        "assessment_type_code": row.assessment.assessment_type_code if row.assessment else None,
        "analysis_text": row.analysis,
        "aggregated_traits": row.aggregated_traits,
        "consistency_scores": row.consistency_scores,
        "trait_gaps": row.trait_gaps,
        "contradictions": row.contradictions,
        "behavioral_patterns": row.behavioral_patterns,
        "assessment_result": (
            {
                "shared_result": result.shared_result_json,
                "type_result": result.type_result_json,
                "narrative": result.narrative,
                "fit_score": result.fit_score,
                "confidence_score": result.confidence_score,
                "risk_flags": result.risk_flags,
            }
            if (
                result := _apply_candidate_scope(
                    db.query(AssessmentResult).filter(AssessmentResult.assessment_id == assessment_id),
                    AssessmentResult,
                    candidate_id,
                ).first()
            )
            else None
        ),
    }


def build_predictions_response(
    db: Session,
    assessment_id: int,
    *,
    candidate_id: int | None = None,
) -> dict[str, Any]:
    _require_assessment(db, assessment_id)
    analysis = _apply_candidate_scope(
        db.query(Analysis).filter(Analysis.assessment_id == assessment_id),
        Analysis,
        candidate_id,
    ).order_by(Analysis.id.desc()).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis found for this assessment.")
    pred = (
        db.query(Predictions)
        .filter(Predictions.analysis_id == analysis.id)
        .first()
    )
    if not pred:
        raise HTTPException(status_code=404, detail="No predictions found for this assessment.")
    return {
        "id": pred.id,
        "analysis_id": pred.analysis_id,
        "candidate_id": analysis.candidate_id,
        "assessment_type_code": analysis.assessment.assessment_type_code if analysis.assessment else None,
        "hiring_recommendation": pred.prediction,
        "fit_score": pred.fit_score,
        "confidence_score": pred.confidence_score,
        "risk_flags": pred.risk_flags,
        "type_result": (
            result.type_result_json
            if (
                result := _apply_candidate_scope(
                    db.query(AssessmentResult).filter(AssessmentResult.assessment_id == assessment_id),
                    AssessmentResult,
                    candidate_id,
                ).first()
            )
            else None
        ),
    }
