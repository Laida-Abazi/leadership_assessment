"""
Intelligence pipeline: segment writing, signal extraction, trait aggregation,
job-fit comparison, and full analysis orchestration.

All functions that call the LLM or write to the DB are designed to be dispatched
as fire-and-forget asyncio tasks — none of them block the WebSocket session.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

import openai
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.db.models import (
    Analysis,
    AssessmentResult,
    Assessments,
    JobRequirements,
    Predictions,
    ResponseSegment,
    ResponseSignal,
)
from app.db.models.job_requirement_profile import JobRequirementProfile
from app.services.assessment_persistence import ensure_responses_row, iter_assessment_answers
from app.services.assessment_scoring import evaluate_assessment

logger = logging.getLogger(__name__)

AnalysisScopeKey = int | tuple[int, int]

_PENDING_SIGNAL_TASKS: dict[AnalysisScopeKey, set[asyncio.Task]] = defaultdict(set)
_ANALYSIS_TASKS: dict[AnalysisScopeKey, asyncio.Task] = {}
_ANALYSIS_STATE: dict[AnalysisScopeKey, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

INTELLIGENCE_MODEL = "gpt-5.2-pro"

# reasoning_effort for each call site:
#   signal extraction runs once per sub-turn segment → "high" balances accuracy and cost.
#   narrative generation runs once per completed interview → "high" balances quality and speed.
REASONING_EFFORT_SIGNAL = "high"
REASONING_EFFORT_NARRATIVE = "high"
REASONING_EFFORT_JOB_PROFILE = "high"
FINAL_ANALYSIS_RETRY_ATTEMPTS = int(os.getenv("FINAL_ANALYSIS_RETRY_ATTEMPTS", "1"))
FINAL_ANALYSIS_RETRY_DELAY_SECONDS = float(os.getenv("FINAL_ANALYSIS_RETRY_DELAY_SECONDS", "5"))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scope_key(assessment_id: int, candidate_id: int | None = None) -> AnalysisScopeKey:
    return assessment_id if candidate_id is None else (assessment_id, candidate_id)


def _apply_candidate_scope(query, model, candidate_id: int | None):
    if candidate_id is None:
        return query.filter(model.candidate_id.is_(None))
    return query.filter(model.candidate_id == candidate_id)


def _set_analysis_state(
    assessment_id: int,
    status: str,
    *,
    candidate_id: int | None = None,
    attempts: int | None = None,
    error: str | None = None,
) -> None:
    key = _scope_key(assessment_id, candidate_id)
    state = dict(_ANALYSIS_STATE.get(key, {}))
    state["status"] = status
    state["updated_at"] = _utcnow_iso()
    state["candidate_id"] = candidate_id
    if attempts is not None:
        state["attempts"] = attempts
    if status == "running" and not state.get("started_at"):
        state["started_at"] = state["updated_at"]
    if status == "completed":
        state["completed_at"] = state["updated_at"]
        state["last_error"] = None
    elif error is not None:
        state["last_error"] = error
    elif status in {"pending", "running"}:
        state["last_error"] = None
    _ANALYSIS_STATE[key] = state


def reset_intelligence_scope(assessment_id: int, *, candidate_id: int | None = None) -> None:
    """Cancel and clear any in-memory intelligence work for this scope."""
    key = _scope_key(assessment_id, candidate_id)

    analysis_task = _ANALYSIS_TASKS.pop(key, None)
    if analysis_task and not analysis_task.done():
        analysis_task.cancel()

    pending_tasks = list(_PENDING_SIGNAL_TASKS.pop(key, set()))
    for task in pending_tasks:
        if not task.done():
            task.cancel()

    _ANALYSIS_STATE.pop(key, None)

# Role-fit recommendation thresholds (tune as needed).
ROLE_READY_MIN_FIT = 0.80
ROLE_PARTIAL_MIN_FIT = 0.60
ROLE_READY_MAX_GAPS = 1
ROLE_PARTIAL_MAX_GAPS = 3

def _get_openai_client() -> openai.OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set")
    return openai.OpenAI(api_key=api_key)


async def _responses_create_async(client: openai.OpenAI, **kwargs):
    """
    The OpenAI SDK client used here is synchronous. Run calls in a worker thread
    so they don't block the event loop (important for websocket responsiveness).
    """
    return await asyncio.to_thread(client.responses.create, **kwargs)


def register_signal_task(assessment_id: int, task: asyncio.Task, *, candidate_id: int | None = None) -> None:
    """Track background signal extraction tasks so final analysis can await them."""
    key = _scope_key(assessment_id, candidate_id)
    tasks = _PENDING_SIGNAL_TASKS[key]
    tasks.add(task)

    def _cleanup(done_task: asyncio.Task) -> None:
        current = _PENDING_SIGNAL_TASKS.get(key)
        if not current:
            return
        current.discard(done_task)
        if not current:
            _PENDING_SIGNAL_TASKS.pop(key, None)

    task.add_done_callback(_cleanup)


def get_intelligence_status(assessment_id: int, *, candidate_id: int | None = None) -> dict[str, Any]:
    """Return background-processing status for an assessment."""
    db = SessionLocal()
    try:
        segment_count = _apply_candidate_scope(
            db.query(ResponseSegment).filter(ResponseSegment.assessment_id == assessment_id),
            ResponseSegment,
            candidate_id,
        ).count()
        signal_count = _apply_candidate_scope(
            db.query(ResponseSignal.response_segment_id).filter(
                ResponseSignal.assessment_id == assessment_id,
                ResponseSignal.response_segment_id.isnot(None),
            ),
            ResponseSignal,
            candidate_id,
        ).distinct().count()
        analysis = _apply_candidate_scope(
            db.query(Analysis).filter(Analysis.assessment_id == assessment_id),
            Analysis,
            candidate_id,
        ).first()
        prediction = None
        if analysis:
            prediction = (
                db.query(Predictions)
                .filter(Predictions.analysis_id == analysis.id)
                .first()
            )
    finally:
        db.close()

    key = _scope_key(assessment_id, candidate_id)
    analysis_task = _ANALYSIS_TASKS.get(key)
    pending_signal_tasks = [
        task
        for task in _PENDING_SIGNAL_TASKS.get(key, set())
        if not task.done()
    ]
    state = dict(_ANALYSIS_STATE.get(key, {}))
    status_value = state.get("status")
    if not status_value:
        if analysis_task and not analysis_task.done():
            status_value = "pending" if pending_signal_tasks else "running"
        elif prediction is not None or analysis is not None:
            status_value = "completed"
        elif pending_signal_tasks or segment_count > 0:
            status_value = "pending"
        else:
            status_value = "pending"
    elif (
        status_value in {"pending", "running"}
        and (not analysis_task or analysis_task.done())
        and not pending_signal_tasks
        and (prediction is not None or analysis is not None)
    ):
        # Prefer durable DB state over stale in-memory flags after reloads/reconnects.
        status_value = "completed"

    return {
        "assessment_id": assessment_id,
        "candidate_id": candidate_id,
        "status": status_value,
        "pending_signal_tasks": len(pending_signal_tasks),
        "segment_count": segment_count,
        "signal_count": signal_count,
        "signals_ready": segment_count == 0 or signal_count >= segment_count,
        "analysis_running": status_value == "running",
        "analysis_ready": analysis is not None,
        "prediction_ready": prediction is not None,
        "failed": status_value == "failed",
        "attempts": state.get("attempts", 0),
        "started_at": state.get("started_at"),
        "completed_at": state.get("completed_at"),
        "updated_at": state.get("updated_at"),
        "last_error": state.get("last_error"),
    }


async def _await_registered_signal_tasks(
    assessment_id: int,
    *,
    candidate_id: int | None = None,
    max_wait_seconds: int = 60,
) -> None:
    """Wait for any in-flight signal extraction tasks registered for this assessment."""
    key = _scope_key(assessment_id, candidate_id)
    deadline = time.monotonic() + max_wait_seconds
    while True:
        pending = [
            task
            for task in _PENDING_SIGNAL_TASKS.get(key, set())
            if not task.done()
        ]
        if not pending:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "[intelligence] timed out waiting for %s signal tasks (assessment_id=%s)",
                len(pending),
                assessment_id,
            )
            return
        await asyncio.wait(
            pending,
            timeout=min(remaining, 5.0),
            return_when=asyncio.ALL_COMPLETED,
        )


def schedule_final_analysis(
    assessment_id: int,
    job_requirements_id: int,
    *,
    candidate_id: int | None = None,
    max_wait_seconds: int = 60,
    retry_attempts: int = FINAL_ANALYSIS_RETRY_ATTEMPTS,
    retry_delay_seconds: float = FINAL_ANALYSIS_RETRY_DELAY_SECONDS,
) -> bool:
    """
    Schedule exactly one final-analysis background task for an assessment.

    The task waits for any registered segment/signal extraction jobs to finish,
    then runs the analysis pipeline once.
    """
    key = _scope_key(assessment_id, candidate_id)
    existing = _ANALYSIS_TASKS.get(key)
    if existing and not existing.done():
        return False

    _set_analysis_state(assessment_id, "pending", candidate_id=candidate_id, attempts=0)

    async def _runner() -> None:
        attempt = 0
        try:
            while True:
                attempt += 1
                try:
                    logger.info(
                        "[intelligence] final analysis scheduled: assessment_id=%s attempt=%s",
                        assessment_id,
                        attempt,
                    )
                    _set_analysis_state(assessment_id, "pending", candidate_id=candidate_id, attempts=attempt)
                    await _await_registered_signal_tasks(
                        assessment_id,
                        candidate_id=candidate_id,
                        max_wait_seconds=max_wait_seconds,
                    )
                    _set_analysis_state(assessment_id, "running", candidate_id=candidate_id, attempts=attempt)
                    await run_full_analysis_chained(
                        assessment_id=assessment_id,
                        job_requirements_id=job_requirements_id,
                        candidate_id=candidate_id,
                        rebuild_from_responses=False,
                        max_wait_seconds=max_wait_seconds,
                    )
                    logger.info(
                        "[intelligence] final analysis completed: assessment_id=%s attempt=%s",
                        assessment_id,
                        attempt,
                    )
                    _set_analysis_state(assessment_id, "completed", candidate_id=candidate_id, attempts=attempt)
                    return
                except Exception as exc:
                    logger.exception(
                        "[intelligence] schedule_final_analysis failed for assessment_id=%s: %s",
                        assessment_id,
                        exc,
                    )
                    if attempt > retry_attempts:
                        _set_analysis_state(
                            assessment_id,
                            "failed",
                            candidate_id=candidate_id,
                            attempts=attempt,
                            error=str(exc),
                        )
                        return
                    _set_analysis_state(
                        assessment_id,
                        "pending",
                        candidate_id=candidate_id,
                        attempts=attempt,
                        error=str(exc),
                    )
                    await asyncio.sleep(retry_delay_seconds)
        finally:
            _ANALYSIS_TASKS.pop(key, None)

    task = asyncio.create_task(
        _runner(),
        name=f"final-analysis-{assessment_id}-{candidate_id or 'assessment'}",
    )
    _ANALYSIS_TASKS[key] = task
    return True


_SIGNAL_EXTRACTION_SYSTEM = (
    "You are an interview assessment analyst. "
    "Analyze interview responses and extract structured role-relevant competency signals. "
    "You MUST return ONLY valid JSON — no preamble, no markdown fences, no explanation."
)

_SIGNAL_EXTRACTION_TEMPLATE = """\
You are an interview assessment analyst. Analyze the following interview response
and extract structured role-relevant signals.

Response type: {response_type}
Candidate response: "{segment_text}"

Return ONLY a JSON object — no preamble, no markdown, no explanation:
{{
  "traits": [list of observed role-relevant competencies, e.g. "stakeholder_alignment", "prioritization", "system_design", "execution_planning"],
  "strengths": [list of demonstrated strengths as short phrases],
  "risks": [list of potential competency gaps or execution risks as short phrases],
  "confidence": float between 0 and 1 representing how clear/complete the signal is
}}"""

_NARRATIVE_SYSTEM = (
    "You are a senior hiring evaluator. "
    "Write a concise, professional role-fit summary based on aggregated interview evidence. "
    "Focus on competencies required by the role, execution quality, and job-requirement alignment. "
    "Avoid personality judgments not supported by evidence."
)


_JOB_PROFILE_SYSTEM = (
    "You are an expert recruiter and interviewer. "
    "Create structured role competency expectations from job requirements."
)

_JOB_PROFILE_TEMPLATE = """\
Given the following job requirements, propose a structured set of role competency expectations.

Return ONLY valid JSON with this exact shape:
{{
  "trait_expectations": {{
    "domain_expertise": ["trait1", "trait2", ...],
    "execution_and_delivery": ["trait1", "trait2", ...],
    "collaboration_and_communication": ["trait1", "trait2", ...],
    "decision_quality": ["trait1", "trait2", ...]
  }},
  "weights": {{
    "domain_expertise": 0.3,
    "execution_and_delivery": 0.3,
    "collaboration_and_communication": 0.2,
    "decision_quality": 0.2
  }}
}}

Constraints:
- traits should be short canonical competency strings (e.g. "roadmap_prioritization", "stakeholder_management", "risk_management", "system_design").
- pick 3-7 traits per category.
- weights must sum to 1.0 (float).
- base your choices on the job text below.

Job requirements text:
{job_requirements_text}
"""


# ---------------------------------------------------------------------------
# 2A — Segment Writer
# ---------------------------------------------------------------------------

async def write_segment(
    db,
    assessment_id: int,
    response_type: str,
    segment_text: str,
    sequence_order: int,
    candidate_id: int | None = None,
    question_id: str | None = None,
) -> ResponseSegment:
    """
    Persist one sub-turn of user speech as a response_segments row.

    Called from the WebSocket handler on every input_transcription.finished=True
    event while a question session is open.
    """
    seg = ResponseSegment(
        assessment_id=assessment_id,
        candidate_id=candidate_id,
        response_type=response_type,
        question_id=question_id,
        segment_text=segment_text,
        sequence_order=sequence_order,
    )
    db.add(seg)
    db.commit()
    db.refresh(seg)
    logger.info(
        "[intelligence] Segment written: id=%s assessment_id=%s type=%s seq=%s len=%s",
        seg.id, assessment_id, response_type, sequence_order, len(segment_text),
    )
    return seg


async def rebuild_segments_and_signals_from_responses(
    assessment_id: int,
    *,
    candidate_id: int | None = None,
) -> tuple[int, int]:
    """
    Rebuild canonical intelligence artifacts from finalized response fields.

    This clears any previously fragmented `response_segments` / `response_signals`
    rows and recreates exactly one segment per populated response field, in the
    canonical interview order. Signals are then re-extracted from those full
    question-level segments so downstream predictions align with the saved answers.
    """
    db = SessionLocal()
    try:
        assessment = db.get(Assessments, assessment_id)
        if not assessment:
            logger.warning(
                "[intelligence] rebuild skipped: no assessment for assessment_id=%s",
                assessment_id,
            )
            return 0, 0
        answer_payloads = iter_assessment_answers(db, assessment, candidate_id=candidate_id)
        if not any((payload.get("answer_text") or "").strip() for payload in answer_payloads):
            logger.warning(
                "[intelligence] rebuild skipped: no saved answers for assessment_id=%s",
                assessment_id,
            )
            return 0, 0

        _apply_candidate_scope(
            db.query(ResponseSignal).filter(ResponseSignal.assessment_id == assessment_id),
            ResponseSignal,
            candidate_id,
        ).delete(synchronize_session=False)
        _apply_candidate_scope(
            db.query(ResponseSegment).filter(ResponseSegment.assessment_id == assessment_id),
            ResponseSegment,
            candidate_id,
        ).delete(synchronize_session=False)
        db.flush()

        rebuilt_segments: list[tuple[int, str, str]] = []
        sequence_order = 0
        for payload in answer_payloads:
            text = (payload.get("answer_text") or "").strip()
            if not text:
                continue
            sequence_order += 1
            response_type = payload["item_key"]
            seg = ResponseSegment(
                assessment_id=assessment_id,
                candidate_id=candidate_id,
                response_type=response_type,
                question_id=payload["item_key"],
                segment_text=text,
                sequence_order=sequence_order,
            )
            db.add(seg)
            db.flush()
            rebuilt_segments.append((seg.id, response_type, text))

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    signal_count = 0
    for seg_id, response_type, text in rebuilt_segments:
        sig = await extract_signals_for_segment(
            segment_id=seg_id,
            assessment_id=assessment_id,
            candidate_id=candidate_id,
            response_type=response_type,
            segment_text=text,
        )
        if sig is not None:
            signal_count += 1

    logger.info(
        "[intelligence] rebuild complete: assessment_id=%s segments=%s signals=%s",
        assessment_id,
        len(rebuilt_segments),
        signal_count,
    )
    return len(rebuilt_segments), signal_count


# ---------------------------------------------------------------------------
# 2B — Signal Extractor
# ---------------------------------------------------------------------------

async def extract_signals_for_segment(
    segment_id: int,
    assessment_id: int,
    candidate_id: int | None,
    response_type: str,
    segment_text: str,
) -> ResponseSignal | None:
    """
    Call GPT-5.2 to extract behavioral signals from one segment and persist them.

    On any failure (network, parse, etc.) logs the error and returns None so the
    caller's fire-and-forget pattern remains safe.
    """
    logger.info(
        "[intelligence] extract_signals_for_segment start: segment_id=%s assessment_id=%s response_type=%s",
        segment_id,
        assessment_id,
        response_type,
    )

    # Fast dedupe guard: if this segment already has a signal, skip re-extraction.
    db = SessionLocal()
    try:
        segment_exists = (
            db.query(ResponseSegment.id)
            .filter(ResponseSegment.id == segment_id)
            .first()
        )
        if not segment_exists:
            logger.info(
                "[intelligence] extract_signals_for_segment skip missing segment: segment_id=%s",
                segment_id,
            )
            return None
        existing = (
            db.query(ResponseSignal)
            .filter(ResponseSignal.response_segment_id == segment_id)
            .first()
        )
        if existing:
            logger.info(
                "[intelligence] extract_signals_for_segment skip existing: segment_id=%s signal_id=%s",
                segment_id,
                existing.id,
            )
            return existing
    finally:
        db.close()

    prompt = _SIGNAL_EXTRACTION_TEMPLATE.format(
        response_type=response_type,
        segment_text=segment_text,
    )

    try:
        client = _get_openai_client()
        response = await _responses_create_async(
            client,
            model=INTELLIGENCE_MODEL,
            max_output_tokens=512,
            reasoning={"effort": REASONING_EFFORT_SIGNAL},
            instructions=_SIGNAL_EXTRACTION_SYSTEM,
            input=prompt,
        )
        raw = (response.output_text or "").strip()
    except Exception as exc:
        logger.exception("[intelligence] LLM call failed for segment_id=%s: %s", segment_id, exc)
        return None

    try:
        payload: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        # Attempt to strip accidental markdown fences
        cleaned = raw.strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.error(
                "[intelligence] JSON parse failed for segment_id=%s. raw=%r",
                segment_id, raw[:300],
            )
            return None
    logger.info(
        "[intelligence] extract_signals_for_segment parsed JSON: segment_id=%s traits=%s strengths=%s risks=%s confidence=%s",
        segment_id,
        len(payload.get("traits") or []),
        len(payload.get("strengths") or []),
        len(payload.get("risks") or []),
        payload.get("confidence"),
    )

    db = SessionLocal()
    try:
        segment_exists = (
            db.query(ResponseSegment.id)
            .filter(ResponseSegment.id == segment_id)
            .first()
        )
        if not segment_exists:
            logger.info(
                "[intelligence] extract_signals_for_segment skip missing-on-write: segment_id=%s",
                segment_id,
            )
            return None

        # Re-check inside write transaction to avoid duplicates under concurrency.
        existing = (
            db.query(ResponseSignal)
            .filter(ResponseSignal.response_segment_id == segment_id)
            .first()
        )
        if existing:
            logger.info(
                "[intelligence] extract_signals_for_segment skip existing-on-write: segment_id=%s signal_id=%s",
                segment_id,
                existing.id,
            )
            return existing

        sig = ResponseSignal(
            response_segment_id=segment_id,
            assessment_id=assessment_id,
            candidate_id=candidate_id,
            response_type=response_type,
            traits=payload.get("traits"),
            strengths=payload.get("strengths"),
            risks=payload.get("risks"),
            confidence=payload.get("confidence"),
        )
        db.add(sig)
        db.commit()
        db.refresh(sig)
        logger.info(
            "[intelligence] Signal extracted: id=%s segment_id=%s traits=%s confidence=%s",
            sig.id, segment_id, sig.traits, sig.confidence,
        )
        return sig
    except IntegrityError as exc:
        db.rollback()
        logger.warning(
            "[intelligence] Signal write skipped due to stale segment reference: segment_id=%s error=%s",
            segment_id,
            exc,
        )
        return None
    except Exception as exc:
        logger.exception("[intelligence] DB write failed for signal (segment_id=%s): %s", segment_id, exc)
        db.rollback()
        return None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2C — Trait Aggregator
# ---------------------------------------------------------------------------

async def aggregate_traits_for_assessment(assessment_id: int, *, candidate_id: int | None = None) -> dict:
    """
    Query all response_signals for this assessment and aggregate:
      - trait frequency and weighted confidence
      - consistency score per trait (stddev of confidence values)
      - contradictions: traits appearing in both traits[] and risks[] across segments
      - behavioral patterns: top traits per response_type

    Returns a dict suitable for Analysis.aggregated_traits.
    """
    db = SessionLocal()
    try:
        signals = (
            _apply_candidate_scope(
                db.query(ResponseSignal).filter(ResponseSignal.assessment_id == assessment_id),
                ResponseSignal,
                candidate_id,
            )
            .all()
        )
    finally:
        db.close()

    if not signals:
        return {}

    # trait_name → list of confidence values from segments where it appeared
    trait_confidences: dict[str, list[float]] = defaultdict(list)
    # trait_name → count of appearances
    trait_count: Counter = Counter()
    # risk phrase → count
    risk_count: Counter = Counter()
    # response_type → trait list
    type_traits: dict[str, list[str]] = defaultdict(list)

    for sig in signals:
        conf = sig.confidence or 0.5
        for t in (sig.traits or []):
            trait_confidences[t].append(conf)
            trait_count[t] += 1
            if sig.response_type:
                type_traits[sig.response_type].append(t)
        for r in (sig.risks or []):
            risk_count[r] += 1

    # Weighted aggregate: mean confidence per trait
    aggregated: dict[str, dict] = {}
    for trait, conf_list in trait_confidences.items():
        mean_conf = sum(conf_list) / len(conf_list)
        stddev = statistics.stdev(conf_list) if len(conf_list) > 1 else 0.0
        aggregated[trait] = {
            "count": trait_count[trait],
            "mean_confidence": round(mean_conf, 4),
            "consistency": round(1.0 - min(stddev, 1.0), 4),
        }

    # Contradictions: trait appears in traits[] of some segment AND in risks[] of another
    trait_names = set(trait_confidences.keys())
    risk_names = set(risk_count.keys())
    contradictions = []
    for t in trait_names & risk_names:
        contradictions.append({
            "trait": t,
            "appeared_as_strength_count": trait_count[t],
            "appeared_as_risk_count": risk_count[t],
        })

    # Behavioral patterns: top 3 traits per response_type
    behavioral_patterns: dict[str, list[str]] = {}
    for rtype, traits in type_traits.items():
        freq = Counter(traits)
        behavioral_patterns[rtype] = [t for t, _ in freq.most_common(3)]

    # Consistency scores: per-trait consistency
    consistency_scores = {t: data["consistency"] for t, data in aggregated.items()}

    return {
        "aggregated_traits": aggregated,
        "consistency_scores": consistency_scores,
        "behavioral_patterns": behavioral_patterns,
        "contradictions": contradictions,
        "top_traits": [t for t, _ in Counter(trait_count).most_common(10)],
    }


# ---------------------------------------------------------------------------
# 2D — Job Fit Comparator
# ---------------------------------------------------------------------------

async def compare_traits_to_job_profile(
    aggregated_data: dict,
    job_requirements_id: int,
) -> dict:
    """
    Load the job_requirement_profiles row for this job_requirements_id,
    compare observed aggregated traits against trait_expectations, and return
    a dict containing trait_gaps and a weighted fit_score (0.0 – 1.0).
    """
    profile = await _ensure_job_requirement_profile(job_requirements_id)

    trait_expectations: dict = profile.trait_expectations or {}
    weights: dict = profile.weights or {}

    observed_traits = set((aggregated_data.get("aggregated_traits") or {}).keys())

    coverage_by_category: dict[str, dict] = {}
    total_weighted_coverage = 0.0
    total_weight = 0.0

    for category, expected_traits in trait_expectations.items():
        if not expected_traits:
            continue
        observed_in_cat = [t for t in expected_traits if t in observed_traits]
        missing_in_cat = [t for t in expected_traits if t not in observed_traits]
        coverage = len(observed_in_cat) / len(expected_traits)
        weight = weights.get(category, 1.0 / max(len(trait_expectations), 1))
        coverage_by_category[category] = {
            "coverage_score": round(coverage, 4),
            "observed": observed_in_cat,
            "gap": missing_in_cat,
            "weight": weight,
        }
        total_weighted_coverage += coverage * weight
        total_weight += weight

    fit_score = (total_weighted_coverage / total_weight) if total_weight > 0 else 0.0

    # Flatten gaps for storage in Analysis.trait_gaps
    trait_gaps = {cat: data["gap"] for cat, data in coverage_by_category.items()}

    return {
        "fit_score": round(fit_score, 4),
        "trait_gaps": trait_gaps,
        "coverage_by_category": coverage_by_category,
    }


async def _ensure_job_requirement_profile(job_requirements_id: int) -> JobRequirementProfile:
    """
    Ensure a job_requirement_profiles row exists for this job_requirements_id.

    If missing, generate it from JobRequirements using the same OpenAI model.
    This keeps the chained prediction pipeline fully end-to-end.
    """
    db = SessionLocal()
    try:
        existing = (
            db.query(JobRequirementProfile)
            .filter(JobRequirementProfile.job_requirements_id == job_requirements_id)
            .first()
        )
        if existing:
            return existing

        job = db.get(JobRequirements, job_requirements_id)
        if not job:
            raise RuntimeError(f"JobRequirements not found for id={job_requirements_id}")

        job_text = "\n".join(
            [
                f"job_id: {job.job_id}",
                f"requirement: {job.requirement}",
                f"skill: {job.skill}",
                f"soft_skill: {job.soft_skill}",
                f"experience: {job.experience}",
                f"education: {job.education}",
                f"certification: {job.certification}",
                f"responsibility: {job.responsibility}",
                f"language: {job.language}",
                f"industry_experience: {job.industry_experience}",
                f"role_experience: {job.role_experience}",
                f"location: {job.location}",
                f"availability: {job.availability}",
                f"work_authorization: {job.work_authorization}",
                f"seniority_level: {job.seniority_level}",
                f"culture_fit: {job.culture_fit}",
            ]
        ).strip()
    finally:
        db.close()

    logger.info(
        "[intelligence] ensuring JobRequirementProfile (job_requirements_id=%s) via LLM",
        job_requirements_id,
    )
    prompt = _JOB_PROFILE_TEMPLATE.format(job_requirements_text=job_text)
    client = _get_openai_client()
    response = await _responses_create_async(
        client,
        model=INTELLIGENCE_MODEL,
        max_output_tokens=800,
        reasoning={"effort": REASONING_EFFORT_JOB_PROFILE},
        instructions=_JOB_PROFILE_SYSTEM,
        input=prompt,
    )
    raw = (response.output_text or "").strip()

    # Parse JSON robustly.
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        cleaned = raw.strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
        data = json.loads(cleaned)

    trait_expectations = data.get("trait_expectations") or {}
    weights = data.get("weights") or {}

    # Normalize weights if they don't sum exactly to 1.
    try:
        total = float(sum(weights.values())) if isinstance(weights, dict) else 0.0
    except Exception:
        total = 0.0
    if total > 0 and abs(total - 1.0) > 1e-6:
        weights = {k: float(v) / total for k, v in weights.items()}

    db = SessionLocal()
    try:
        existing = (
            db.query(JobRequirementProfile)
            .filter(JobRequirementProfile.job_requirements_id == job_requirements_id)
            .first()
        )
        if existing:
            logger.info(
                "[intelligence] JobRequirementProfile already exists (job_requirements_id=%s profile_id=%s)",
                job_requirements_id,
                existing.id,
            )
            return existing

        profile = JobRequirementProfile(
            job_requirements_id=job_requirements_id,
            trait_expectations=trait_expectations,
            weights=weights,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        logger.info(
            "[intelligence] JobRequirementProfile created (job_requirements_id=%s categories=%s)",
            job_requirements_id,
            list(trait_expectations.keys()),
        )
        return profile
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2E — Analysis Writer (full pipeline)
# ---------------------------------------------------------------------------

async def run_full_analysis(
    assessment_id: int,
    job_requirements_id: int,
    *,
    candidate_id: int | None = None,
) -> None:
    """
    Orchestrate the full intelligence pipeline after an interview completes:

    1. Aggregate traits across all response_signals for this assessment.
    2. Compare aggregated traits to the job_requirement_profile.
    3. Generate a narrative LLM summary.
    4. Upsert the Analysis row with all JSONB fields.
    5. Upsert the Predictions row with fit_score, confidence_score, risk_flags.
    """
    logger.info(
        "[intelligence] run_full_analysis START: assessment_id=%s job_requirements_id=%s",
        assessment_id, job_requirements_id,
    )

    # Step 1 — aggregate
    logger.info("[intelligence] Step 1/5 aggregate_traits_for_assessment start (assessment_id=%s)", assessment_id)
    aggregated_data = await aggregate_traits_for_assessment(assessment_id, candidate_id=candidate_id)
    agg_traits = aggregated_data.get("aggregated_traits") or {}
    logger.info(
        "[intelligence] Step 1/5 aggregate_traits_for_assessment done (assessment_id=%s traits=%s)",
        assessment_id,
        len(agg_traits),
    )

    # Step 2-3 — type-specific evaluation on top of shared aggregation.
    logger.info("[intelligence] Step 2-3/5 evaluation start (assessment_id=%s)", assessment_id)
    evaluation = await evaluate_assessment(
        assessment_id=assessment_id,
        job_requirements_id=job_requirements_id,
        candidate_id=candidate_id,
        aggregated_data=aggregated_data,
        compare_traits_to_job_profile=compare_traits_to_job_profile,
        generate_narrative=_generate_narrative,
        get_openai_client=_get_openai_client,
        responses_create_async=_responses_create_async,
    )
    logger.info(
        "[intelligence] Step 2-3/5 evaluation done (assessment_id=%s fit_score=%s)",
        assessment_id,
        evaluation.shared_result.get("fit_score"),
    )

    # Steps 4 & 5 — persist
    logger.info("[intelligence] Step 4-5/5 _upsert_analysis start (assessment_id=%s)", assessment_id)
    db = SessionLocal()
    try:
        _upsert_analysis(
            db=db,
            assessment_id=assessment_id,
            job_requirements_id=job_requirements_id,
            candidate_id=candidate_id,
            aggregated_data=aggregated_data,
            shared_result=evaluation.shared_result,
            type_result=evaluation.type_result,
            narrative=evaluation.narrative,
            prediction_text=evaluation.prediction_text,
        )
        logger.info(
            "[intelligence] Step 4-5/5 _upsert_analysis done (assessment_id=%s fit_score=%s)",
            assessment_id, evaluation.shared_result.get("fit_score"),
        )
    except Exception as exc:
        logger.exception("[intelligence] run_full_analysis FAILED (persist): %s", exc)
        db.rollback()
        raise
    finally:
        db.close()


async def run_full_analysis_chained(
    assessment_id: int,
    job_requirements_id: int,
    *,
    candidate_id: int | None = None,
    rebuild_from_responses: bool = False,
    grace_period_seconds: float = 3.0,
    max_wait_seconds: int = 60,
) -> None:
    """
    Chained orchestration — designed to be fast when signals were already
    extracted during the interview:

    1. Quick check: if every segment already has a signal → proceed immediately.
    2. Otherwise, wait a brief grace period for in-flight extractions, then
       actively extract any remaining missing signals in parallel.
    3. Run the normal full analysis pipeline.
    """
    logger.info(
        "[intelligence] run_full_analysis_chained START: assessment_id=%s job_requirements_id=%s rebuild_from_responses=%s",
        assessment_id,
        job_requirements_id,
        rebuild_from_responses,
    )

    def _check_signals() -> tuple[int, list[int]]:
        """Return (segment_count, missing_segment_ids)."""
        db = SessionLocal()
        try:
            seg_rows = (
                _apply_candidate_scope(
                    db.query(ResponseSegment.id).filter(ResponseSegment.assessment_id == assessment_id),
                    ResponseSegment,
                    candidate_id,
                )
                .all()
            )
            seg_ids = {r[0] for r in seg_rows}
            sig_segment_ids = (
                _apply_candidate_scope(
                    db.query(ResponseSignal.response_segment_id).filter(
                        ResponseSignal.assessment_id == assessment_id,
                        ResponseSignal.response_segment_id.isnot(None),
                    ),
                    ResponseSignal,
                    candidate_id,
                )
                .distinct()
                .all()
            )
            sig_ids = {r[0] for r in sig_segment_ids}
            return len(seg_ids), list(seg_ids - sig_ids)
        finally:
            db.close()

    if rebuild_from_responses:
        rebuilt_segments, rebuilt_signals = await rebuild_segments_and_signals_from_responses(
            assessment_id,
            candidate_id=candidate_id,
        )
        logger.info(
            "[intelligence] chained rebuild result (assessment_id=%s): segments=%s signals=%s",
            assessment_id,
            rebuilt_segments,
            rebuilt_signals,
        )

    # ── Fast path: all signals already extracted during the interview ─────────
    seg_count, missing_ids = _check_signals()
    logger.info(
        "[intelligence] chained initial check (assessment_id=%s): segments=%s missing_signals=%s",
        assessment_id, seg_count, len(missing_ids),
    )

    if seg_count == 0:
        logger.warning(
            "[intelligence] run_full_analysis_chained: no response_segments for assessment_id=%s. "
            "Attempting bootstrap from legacy responses.",
            assessment_id,
        )
        created = await _bootstrap_segments_from_legacy_responses(assessment_id, candidate_id=candidate_id)
        if created > 0:
            logger.info(
                "[intelligence] bootstrap created %s response_segments (assessment_id=%s)",
                created, assessment_id,
            )
            seg_count, missing_ids = _check_signals()
        else:
            logger.warning(
                "[intelligence] bootstrap found no legacy response text for assessment_id=%s. "
                "Running analysis/prediction with empty signals.",
                assessment_id,
            )

    # ── Grace period: let in-flight extraction tasks finish ───────────────────
    if missing_ids:
        logger.info(
            "[intelligence] chained grace wait %.1fs for %s in-flight signals (assessment_id=%s)",
            grace_period_seconds, len(missing_ids), assessment_id,
        )
        await asyncio.sleep(grace_period_seconds)
        seg_count, missing_ids = _check_signals()
        logger.info(
            "[intelligence] chained after grace (assessment_id=%s): segments=%s missing_signals=%s",
            assessment_id, seg_count, len(missing_ids),
        )

    # ── Active catch-up: extract any still-missing signals in parallel ────────
    if missing_ids:
        logger.info(
            "[intelligence] chained extracting %s missing signals (assessment_id=%s)",
            len(missing_ids), assessment_id,
        )
        semaphore = asyncio.Semaphore(4)

        async def _extract_one(seg_id: int) -> None:
            async with semaphore:
                db = SessionLocal()
                try:
                    seg = db.query(ResponseSegment).filter(ResponseSegment.id == seg_id).first()
                    if not seg:
                        return
                    await extract_signals_for_segment(
                        segment_id=seg.id,
                        assessment_id=assessment_id,
                        candidate_id=candidate_id,
                        response_type=seg.response_type,
                        segment_text=seg.segment_text,
                    )
                finally:
                    db.close()

        await asyncio.gather(*[_extract_one(sid) for sid in missing_ids])

    seg_count, still_missing = _check_signals()
    if still_missing:
        logger.warning(
            "[intelligence] chained proceeding with %s signals still missing (assessment_id=%s)",
            len(still_missing), assessment_id,
        )
    else:
        logger.info(
            "[intelligence] chained all signals ready (assessment_id=%s segments=%s)",
            assessment_id, seg_count,
        )

    logger.info(
        "[intelligence] chained starting run_full_analysis (assessment_id=%s)",
        assessment_id,
    )
    await run_full_analysis(assessment_id, job_requirements_id, candidate_id=candidate_id)


async def _bootstrap_segments_from_legacy_responses(
    assessment_id: int,
    *,
    candidate_id: int | None = None,
) -> int:
    """
    Create response_segments from existing Responses.*_response text for assessments
    created before granular segment capture was enabled.
    """
    db = SessionLocal()
    try:
        # Avoid duplicating bootstrap if segments already exist.
        existing = (
            _apply_candidate_scope(
                db.query(ResponseSegment.id).filter(ResponseSegment.assessment_id == assessment_id),
                ResponseSegment,
                candidate_id,
            )
            .first()
        )
        if existing:
            return 0

        assessment = db.get(Assessments, assessment_id)
        if not assessment:
            return 0
        answer_payloads = iter_assessment_answers(db, assessment, candidate_id=candidate_id)

        created = 0
        sequence_order = 0
        for payload in answer_payloads:
            text = (payload.get("answer_text") or "").strip()
            if not text:
                continue
            # Keep chunks reasonably sized for signal extraction quality.
            chunks = _chunk_text(text, chunk_size=700)
            response_type = payload["item_key"]
            for chunk in chunks:
                sequence_order += 1
                seg = ResponseSegment(
                    assessment_id=assessment_id,
                    candidate_id=candidate_id,
                    response_type=response_type,
                    question_id=payload["item_key"],
                    segment_text=chunk,
                    sequence_order=sequence_order,
                )
                db.add(seg)
                db.flush()
                created += 1
                logger.info(
                    "[intelligence] bootstrap segment created: assessment_id=%s segment_id=%s type=%s seq=%s",
                    assessment_id,
                    seg.id,
                    response_type,
                    sequence_order,
                )
        db.commit()
        return created
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _chunk_text(text: str, chunk_size: int = 700) -> list[str]:
    """
    Split long response text into near-sentence chunks so one legacy response can
    generate multiple response_segments.
    """
    s = (text or "").strip()
    if not s:
        return []
    if len(s) <= chunk_size:
        return [s]

    chunks: list[str] = []
    cursor = 0
    while cursor < len(s):
        end = min(cursor + chunk_size, len(s))
        if end < len(s):
            # Prefer sentence boundary, fallback to last whitespace.
            period = s.rfind(". ", cursor, end)
            split_at = period + 1 if period > cursor else s.rfind(" ", cursor, end)
            if split_at > cursor:
                end = split_at
        chunk = s[cursor:end].strip()
        if chunk:
            chunks.append(chunk)
        cursor = end
    return chunks


# ---------------------------------------------------------------------------
# Internal helpers for run_full_analysis
# ---------------------------------------------------------------------------

async def _generate_narrative(aggregated_data: dict, fit_data: dict) -> str:
    """Call GPT-5.2 to produce a role-fit narrative summary."""
    top_traits = aggregated_data.get("top_traits", [])
    contradictions = aggregated_data.get("contradictions", [])
    fit_score = fit_data.get("fit_score")
    trait_gaps = fit_data.get("trait_gaps", {})

    user_content = (
        f"Top observed competencies: {', '.join(top_traits) or 'none detected'}.\n"
        f"Observed contradictions/gaps: {json.dumps(contradictions)}.\n"
        f"Job fit score: {fit_score if fit_score is not None else 'not computed (no profile)'}.\n"
        f"Competency gaps by category: {json.dumps(trait_gaps)}.\n\n"
        "Write a concise 2-4 paragraph professional summary focused on role fit: "
        "where competency evidence is strong, where gaps remain, and whether evidence supports readiness for this role. "
        "Adapt naturally to the role type (technical, product, operations, people, etc.) based on the job requirements."
    )

    try:
        client = _get_openai_client()
        response = await _responses_create_async(
            client,
            model=INTELLIGENCE_MODEL,
            max_output_tokens=1024,
            reasoning={"effort": REASONING_EFFORT_NARRATIVE},
            instructions=_NARRATIVE_SYSTEM,
            input=user_content,
        )
        return (response.output_text or "").strip()
    except Exception as exc:
        logger.exception("[intelligence] Narrative generation failed: %s", exc)
        return "Narrative generation failed due to an internal error."


def _is_assessment_result_integrity_error(exc: IntegrityError) -> bool:
    """Return True when a DB write failed on an assessment_results uniqueness check."""
    statement = (getattr(exc, "statement", "") or "").lower()
    message = str(getattr(exc, "orig", exc)).lower()
    return (
        "assessment_results" in statement
        and ("duplicate key value violates unique constraint" in message or "unique constraint failed" in message)
    )


def _upsert_analysis(
    db,
    assessment_id: int,
    job_requirements_id: int,
    candidate_id: int | None,
    aggregated_data: dict,
    shared_result: dict,
    type_result: dict,
    narrative: str,
    prediction_text: str,
    *,
    _retry_on_result_conflict: bool = True,
) -> None:
    """Write or update the Analysis and Predictions rows for this assessment."""
    analysis_row = _apply_candidate_scope(
        db.query(Analysis).filter(Analysis.assessment_id == assessment_id),
        Analysis,
        candidate_id,
    ).first()

    agg_traits = aggregated_data.get("aggregated_traits")
    consistency = aggregated_data.get("consistency_scores")
    contradictions = aggregated_data.get("contradictions")
    patterns = aggregated_data.get("behavioral_patterns")
    trait_gaps = shared_result.get("trait_gaps")

    if analysis_row is None:
        responses_row = ensure_responses_row(db, assessment_id, candidate_id=candidate_id)
        responses_id = responses_row.id
        logger.info(
            "[intelligence] _upsert_analysis creating Analysis (assessment_id=%s responses_id=%s)",
            assessment_id,
            responses_id,
        )
        analysis_row = Analysis(
            assessment_id=assessment_id,
            candidate_id=candidate_id,
            job_requirements_id=job_requirements_id,
            responses_id=responses_id,
            analysis=narrative,
            aggregated_traits=agg_traits,
            consistency_scores=consistency,
            trait_gaps=trait_gaps,
            contradictions=contradictions,
            behavioral_patterns=patterns,
        )
        db.add(analysis_row)
        db.flush()
    else:
        logger.info(
            "[intelligence] _upsert_analysis updating Analysis (assessment_id=%s analysis_id=%s)",
            assessment_id,
            analysis_row.id,
        )
        analysis_row.analysis = narrative
        analysis_row.aggregated_traits = agg_traits
        analysis_row.consistency_scores = consistency
        analysis_row.trait_gaps = trait_gaps
        analysis_row.contradictions = contradictions
        analysis_row.behavioral_patterns = patterns

    db.flush()

    # Upsert Predictions
    pred_row = (
        db.query(Predictions)
        .filter(Predictions.analysis_id == analysis_row.id)
        .first()
    )
    fit_score = shared_result.get("fit_score")
    risk_flags = shared_result.get("risk_flags") or []
    hiring_recommendation = prediction_text or _hiring_recommendation(fit_score, risk_flags)

    if pred_row is None:
        logger.info(
            "[intelligence] _upsert_analysis creating Predictions (assessment_id=%s analysis_id=%s)",
            assessment_id,
            analysis_row.id,
        )
        pred_row = Predictions(
            analysis_id=analysis_row.id,
            prediction=hiring_recommendation,
            fit_score=fit_score,
            confidence_score=shared_result.get("confidence_score", _overall_confidence(aggregated_data)),
            risk_flags=risk_flags,
        )
        db.add(pred_row)
    else:
        logger.info(
            "[intelligence] _upsert_analysis updating Predictions (assessment_id=%s analysis_id=%s prediction_id=%s)",
            assessment_id,
            analysis_row.id,
            pred_row.id,
        )
        pred_row.prediction = hiring_recommendation
        pred_row.fit_score = fit_score
        pred_row.confidence_score = shared_result.get("confidence_score", _overall_confidence(aggregated_data))
        pred_row.risk_flags = risk_flags

    result_row = (
        _apply_candidate_scope(
            db.query(AssessmentResult).filter(AssessmentResult.assessment_id == assessment_id),
            AssessmentResult,
            candidate_id,
        )
        .first()
    )
    if result_row is None:
        result_row = AssessmentResult(
            assessment_id=assessment_id,
            candidate_id=candidate_id,
            shared_result_json=shared_result,
            type_result_json=type_result,
            narrative=narrative,
            fit_score=fit_score,
            confidence_score=shared_result.get("confidence_score", _overall_confidence(aggregated_data)),
            risk_flags=risk_flags,
        )
        db.add(result_row)
    else:
        result_row.shared_result_json = shared_result
        result_row.type_result_json = type_result
        result_row.narrative = narrative
        result_row.fit_score = fit_score
        result_row.confidence_score = shared_result.get("confidence_score", _overall_confidence(aggregated_data))
        result_row.risk_flags = risk_flags

    logger.info(
        "[intelligence] _upsert_analysis prepared commit (assessment_id=%s fit_score=%s confidence_score=%s risk_flags_count=%s)",
        assessment_id,
        fit_score,
        shared_result.get("confidence_score", _overall_confidence(aggregated_data)),
        len(risk_flags or []),
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _retry_on_result_conflict and _is_assessment_result_integrity_error(exc):
            logger.warning(
                "[intelligence] _upsert_analysis retrying after AssessmentResult uniqueness conflict "
                "(assessment_id=%s candidate_id=%s): %s",
                assessment_id,
                candidate_id,
                exc,
            )
            return _upsert_analysis(
                db,
                assessment_id,
                job_requirements_id,
                candidate_id,
                aggregated_data,
                shared_result,
                type_result,
                narrative,
                prediction_text,
                _retry_on_result_conflict=False,
            )
        raise


def _build_risk_flags(aggregated_data: dict, fit_data: dict) -> list[dict]:
    """Build a list of risk flag objects from contradictions and trait gaps."""
    flags: list[dict] = []
    for c in (aggregated_data.get("contradictions") or []):
        flags.append({"type": "contradiction", "detail": c})
    for category, gaps in (fit_data.get("trait_gaps") or {}).items():
        if gaps:
            flags.append({"type": "gap", "category": category, "missing_traits": gaps})
    return flags


def _overall_confidence(aggregated_data: dict) -> float | None:
    """Mean confidence across all aggregated traits."""
    traits = (aggregated_data.get("aggregated_traits") or {}).values()
    confs = [t.get("mean_confidence", 0.5) for t in traits if isinstance(t, dict)]
    # If no traits were aggregated, confidence is effectively 0 (no signal).
    return round(sum(confs) / len(confs), 4) if confs else 0.0


def _hiring_recommendation(fit_score: float | None, risk_flags: list) -> str:
    """Derive a role-readiness recommendation from fit score and competency gaps."""
    gap_count = len([f for f in risk_flags if f.get("type") == "gap"])
    if fit_score is None:
        return "Insufficient evidence to make a role-readiness recommendation."
    if fit_score >= ROLE_READY_MIN_FIT and gap_count <= ROLE_READY_MAX_GAPS:
        return "Role-ready — strong alignment with core role requirements."
    if fit_score >= ROLE_PARTIAL_MIN_FIT and gap_count <= ROLE_PARTIAL_MAX_GAPS:
        return "Partially role-aligned — additional validation recommended."
    return "Not yet role-ready — significant requirement gaps remain."
