from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.db import SessionLocal
from app.db.models import Assessments
from app.services.assessment_persistence import iter_assessment_answers
from app.services.assessment_registry import get_assessment_definition

logger = logging.getLogger(__name__)

INTELLIGENCE_MODEL = "gpt-5.2-pro"


@dataclass
class AssessmentEvaluation:
    shared_result: dict
    type_result: dict
    narrative: str
    prediction_text: str


async def evaluate_assessment(
    *,
    assessment_id: int,
    job_requirements_id: int,
    candidate_id: int | None,
    aggregated_data: dict,
    compare_traits_to_job_profile,
    generate_narrative,
    get_openai_client,
    responses_create_async,
) -> AssessmentEvaluation:
    db = SessionLocal()
    try:
        assessment = db.get(Assessments, assessment_id)
        if assessment is None:
            raise RuntimeError(f"Assessment {assessment_id} not found")
        definition = get_assessment_definition(assessment.assessment_type_code)
        answers = iter_assessment_answers(db, assessment, candidate_id=candidate_id)
        answer_block = _build_answer_block(answers)
    finally:
        db.close()

    if definition.code == "leadership_core":
        fit_data = await compare_traits_to_job_profile(aggregated_data, job_requirements_id)
        narrative = await generate_narrative(aggregated_data, fit_data)
        shared_result = {
            "assessment_type_code": definition.code,
            "fit_score": fit_data.get("fit_score"),
            "confidence_score": _overall_confidence(aggregated_data),
            "risk_flags": _build_risk_flags(aggregated_data, fit_data),
            "trait_gaps": fit_data.get("trait_gaps"),
            "coverage_by_category": fit_data.get("coverage_by_category"),
        }
        type_result = {
            "top_traits": aggregated_data.get("top_traits") or [],
            "behavioral_patterns": aggregated_data.get("behavioral_patterns") or {},
            "contradictions": aggregated_data.get("contradictions") or [],
        }
        prediction_text = _hiring_recommendation(
            shared_result.get("fit_score"),
            shared_result.get("risk_flags") or [],
        )
        return AssessmentEvaluation(
            shared_result=shared_result,
            type_result=type_result,
            narrative=narrative,
            prediction_text=prediction_text,
        )

    prompt = _build_type_specific_prompt(
        assessment_name=definition.name,
        description=definition.description,
        answer_block=answer_block,
        aggregated_data=aggregated_data,
        assessment_code=definition.code,
    )
    client = get_openai_client()
    try:
        response = await responses_create_async(
            client,
            model=INTELLIGENCE_MODEL,
            max_output_tokens=1400,
            reasoning={"effort": "high"},
            instructions=_type_specific_system_instruction(definition.code),
            input=prompt,
        )
        payload = _parse_json_payload(response.output_text or "")
    except Exception as exc:
        logger.exception("Type-specific evaluation failed for assessment_id=%s: %s", assessment_id, exc)
        payload = _fallback_payload(definition.code)

    shared_result = payload.get("shared_result") or {}
    shared_result.setdefault("assessment_type_code", definition.code)
    shared_result.setdefault("confidence_score", payload.get("confidence"))
    shared_result.setdefault("risk_flags", payload.get("risk_flags") or [])
    shared_result.setdefault("fit_score", payload.get("fit_score"))

    type_result = payload.get("type_result") or {}
    narrative = (payload.get("narrative") or "").strip()
    prediction_text = (payload.get("prediction_text") or "").strip()

    if not narrative:
        narrative = _fallback_payload(definition.code)["narrative"]
    if not prediction_text:
        prediction_text = _fallback_payload(definition.code)["prediction_text"]

    return AssessmentEvaluation(
        shared_result=shared_result,
        type_result=type_result,
        narrative=narrative,
        prediction_text=prediction_text,
    )


def _build_answer_block(answers: list[dict]) -> str:
    chunks = []
    for answer in answers:
        if not answer.get("answer_text"):
            continue
        chunks.append(
            f"[{answer['display_label']} - {answer['item_key']}]\n"
            f"Q: {answer['question_text']}\n"
            f"A: {answer['answer_text']}"
        )
    return "\n\n".join(chunks) if chunks else "No answers recorded."


def _build_type_specific_prompt(
    *,
    assessment_name: str,
    description: str,
    answer_block: str,
    aggregated_data: dict,
    assessment_code: str,
) -> str:
    return f"""Assess the completed {assessment_name} interview.

Assessment type: {assessment_code}
Assessment description:
{description}

Interview answers:
{answer_block}

Observed cross-answer signals:
{json.dumps(aggregated_data, ensure_ascii=True)}

Return ONLY valid JSON in this shape:
{{
  "shared_result": {{
    "fit_score": number between 0 and 1,
    "confidence_score": number between 0 and 1,
    "risk_flags": [{{"type": string, "detail": string}}],
    "summary": string
  }},
  "type_result": object,
  "narrative": string,
  "prediction_text": string
}}

Rules:
- For MBTI, infer a likely four-letter preference code, summarize the four dichotomies, and explicitly frame the result as self-reflection rather than diagnosis.
- For SLII, infer how well the leader adapts style to development level, including strengths, mismatch patterns, and coaching suggestions.
- `fit_score` should still be populated for consistency:
  - MBTI: use a profile clarity/confidence score.
  - SLII: use leadership adaptability/alignment score.
- Keep the narrative concise but specific.
"""


def _type_specific_system_instruction(assessment_code: str) -> str:
    if assessment_code == "mbti":
        return (
            "You are a personality insight analyst. Return only JSON. "
            "Use MBTI as a self-reflection framework, not a diagnostic label."
        )
    return (
        "You are a situational leadership assessment analyst. Return only JSON. "
        "Focus on development-level diagnosis, style flexibility, and coaching value."
    )


def _parse_json_payload(raw: str) -> dict:
    text = _strip_markdown_fences(raw or "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        candidate = _extract_json_object(text)
        if candidate:
            return json.loads(candidate)
        cleaned = _remove_control_chars(text)
        if cleaned != text:
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                candidate = _extract_json_object(cleaned)
                if candidate:
                    return json.loads(candidate)
        raise


def _strip_markdown_fences(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def _remove_control_chars(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text or "")


def _extract_json_object(text: str) -> str | None:
    start = (text or "").find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return None


def _fallback_payload(assessment_code: str) -> dict:
    if assessment_code == "mbti":
        return {
            "shared_result": {
                "fit_score": 0.5,
                "confidence_score": 0.5,
                "risk_flags": [{"type": "caveat", "detail": "Use MBTI outputs as self-reflection guidance, not a diagnostic judgment."}],
            },
            "type_result": {
                "type_code": "UNDETERMINED",
                "dimension_scores": {},
                "growth_edges": [],
            },
            "narrative": "A provisional MBTI-style reflection was generated, but the signal was not strong enough to confidently infer a personality preference profile.",
            "prediction_text": "Reflection profile generated with low confidence; use as a conversation starter only.",
        }
    return {
        "shared_result": {
            "fit_score": 0.5,
            "confidence_score": 0.5,
            "risk_flags": [],
        },
        "type_result": {
            "primary_style": "UNKNOWN",
            "secondary_style": "UNKNOWN",
            "adaptability_score": 0.5,
            "mismatch_patterns": [],
            "coaching_suggestions": [],
        },
        "narrative": "A provisional SLII assessment was generated, but there was not enough clear evidence to strongly determine situational leadership adaptability.",
        "prediction_text": "Leadership adaptability evidence is mixed; additional scenario responses are recommended.",
    }


def _overall_confidence(aggregated_data: dict) -> float | None:
    traits = (aggregated_data.get("aggregated_traits") or {}).values()
    confidences = [item.get("mean_confidence", 0.5) for item in traits if isinstance(item, dict)]
    return round(sum(confidences) / len(confidences), 4) if confidences else 0.0


def _build_risk_flags(aggregated_data: dict, fit_data: dict) -> list[dict]:
    flags: list[dict] = []
    for contradiction in (aggregated_data.get("contradictions") or []):
        flags.append({"type": "contradiction", "detail": contradiction})
    for category, gaps in (fit_data.get("trait_gaps") or {}).items():
        if gaps:
            flags.append({"type": "gap", "category": category, "missing_traits": gaps})
    return flags


def _hiring_recommendation(fit_score: float | None, risk_flags: list) -> str:
    gap_count = len([flag for flag in risk_flags if flag.get("type") == "gap"])
    if fit_score is None:
        return "Insufficient evidence to make a role-readiness recommendation."
    if fit_score >= 0.8 and gap_count <= 1:
        return "Role-ready — strong alignment with core role requirements."
    if fit_score >= 0.6 and gap_count <= 3:
        return "Partially role-aligned — additional validation recommended."
    return "Not yet role-ready — significant requirement gaps remain."
