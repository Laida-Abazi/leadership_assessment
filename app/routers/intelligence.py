"""
Intelligence REST endpoints — expose response segments, signals, analysis,
and predictions produced by the intelligence pipeline.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.db.models import Analysis, AssessmentResult, Assessments, Predictions
from app.db.models.response_segment import ResponseSegment
from app.db.models.response_signal import ResponseSignal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intelligence", tags=["intelligence"])
_RERUN_TASKS: dict[int, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# GET /intelligence/assessment/{assessment_id}/segments
# ---------------------------------------------------------------------------

@router.get("/assessment/{assessment_id}/segments")
def get_segments(assessment_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Return all response_segments for this assessment ordered by sequence_order."""
    _require_assessment(db, assessment_id)
    rows = (
        db.query(ResponseSegment)
        .filter(ResponseSegment.assessment_id == assessment_id)
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
def get_signals(assessment_id: int, db: Session = Depends(get_db)) -> dict[str, list[dict]]:
    """Return response_signals grouped by response_type."""
    _require_assessment(db, assessment_id)
    rows = (
        db.query(ResponseSignal)
        .filter(ResponseSignal.assessment_id == assessment_id)
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
def get_analysis(assessment_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return the full Analysis row including all JSONB intelligence fields."""
    return build_analysis_response(db, assessment_id)


# ---------------------------------------------------------------------------
# GET /intelligence/assessment/{assessment_id}/predictions
# ---------------------------------------------------------------------------

@router.get("/assessment/{assessment_id}/predictions")
def get_predictions(assessment_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return the Predictions row including fit_score, risk_flags, and hiring_recommendation."""
    return build_predictions_response(db, assessment_id)


@router.get("/assessment/{assessment_id}/status")
def get_intelligence_processing_status(
    assessment_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return whether signal extraction / analysis is still running."""
    return build_status_response(db, assessment_id)


# ---------------------------------------------------------------------------
# POST /intelligence/assessment/{assessment_id}/rerun
# ---------------------------------------------------------------------------

@router.post("/assessment/{assessment_id}/rerun", status_code=202)
async def rerun_analysis(
    assessment_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Manually trigger final analysis using already collected segments/signals."""
    assessment = _require_assessment(db, assessment_id)
    jrq_id = assessment.job_requirements_id
    from app.services.intelligence import schedule_final_analysis

    existing = _RERUN_TASKS.get(assessment_id)
    if existing and not existing.done():
        return {
            "status": "accepted",
            "detail": f"run_full_analysis already running for assessment_id={assessment_id}",
        }

    async def _run() -> None:
        try:
            scheduled = schedule_final_analysis(
                assessment_id=assessment_id,
                job_requirements_id=jrq_id,
            )
            if not scheduled:
                logger.info(
                    "[intelligence] final analysis already running for assessment_id=%s",
                    assessment_id,
                )
        except Exception as exc:
            logger.exception(
                "[intelligence] rerun_analysis failed for assessment_id=%s: %s",
                assessment_id, exc,
            )
        finally:
            _RERUN_TASKS.pop(assessment_id, None)

    task = asyncio.create_task(_run(), name=f"rerun-analysis-{assessment_id}")
    _RERUN_TASKS[assessment_id] = task
    # keep signature-compatible with existing endpoint behavior
    background_tasks.add_task(lambda: None)
    return {"status": "accepted", "detail": f"run_full_analysis dispatched for assessment_id={assessment_id}"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_assessment(db: Session, assessment_id: int) -> Assessments:
    assessment = db.get(Assessments, assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail=f"Assessment {assessment_id} not found.")
    return assessment


def build_status_response(db: Session, assessment_id: int) -> dict[str, Any]:
    _require_assessment(db, assessment_id)
    from app.services.intelligence import get_intelligence_status

    return get_intelligence_status(assessment_id)


def build_analysis_response(db: Session, assessment_id: int) -> dict[str, Any]:
    _require_assessment(db, assessment_id)
    row = (
        db.query(Analysis)
        .filter(Analysis.assessment_id == assessment_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Analysis not found for this assessment.")
    return {
        "id": row.id,
        "assessment_id": row.assessment_id,
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
            if (result := db.query(AssessmentResult).filter(AssessmentResult.assessment_id == assessment_id).first())
            else None
        ),
    }


def build_predictions_response(db: Session, assessment_id: int) -> dict[str, Any]:
    _require_assessment(db, assessment_id)
    analysis = (
        db.query(Analysis)
        .filter(Analysis.assessment_id == assessment_id)
        .first()
    )
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
        "assessment_type_code": analysis.assessment.assessment_type_code if analysis.assessment else None,
        "hiring_recommendation": pred.prediction,
        "fit_score": pred.fit_score,
        "confidence_score": pred.confidence_score,
        "risk_flags": pred.risk_flags,
        "type_result": (
            result.type_result_json
            if (result := db.query(AssessmentResult).filter(AssessmentResult.assessment_id == assessment_id).first())
            else None
        ),
    }
