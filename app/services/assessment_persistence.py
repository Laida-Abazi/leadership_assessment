from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.db.models import AssessmentAnswer, AssessmentItem, Assessments, Responses
from app.services.assessment_registry import (
    LEADERSHIP_ITEM_TEMPLATES,
    build_assessment_items,
    get_assessment_definition,
)


LEGACY_RESPONSE_FIELD_BY_ITEM_KEY = {
    item.key: item.key.replace("_question", "_response")
    for item in LEADERSHIP_ITEM_TEMPLATES
}


def ensure_responses_row(db: Session, assessment_id: int) -> Responses:
    row = db.query(Responses).filter(Responses.assessment_id == assessment_id).first()
    if row is None:
        row = Responses(assessment_id=assessment_id)
        db.add(row)
        db.flush()
    return row


def ensure_responses_row_for_assessment(assessment_id: int) -> None:
    db = SessionLocal()
    try:
        ensure_responses_row(db, assessment_id)
        db.commit()
    finally:
        db.close()


def sync_assessment_items(
    db: Session,
    assessment: Assessments,
    items: list[dict[str, Any]],
) -> list[AssessmentItem]:
    existing = {
        row.item_key: row
        for row in db.query(AssessmentItem).filter(AssessmentItem.assessment_id == assessment.id).all()
    }
    seen: set[str] = set()
    out: list[AssessmentItem] = []
    for item in items:
        row = existing.get(item["item_key"])
        if row is None:
            row = AssessmentItem(assessment_id=assessment.id, **item)
            db.add(row)
        else:
            row.display_label = item["display_label"]
            row.item_order = item["item_order"]
            row.item_kind = item["item_kind"]
            row.prompt_text = item["prompt_text"]
            row.item_meta = item.get("item_meta") or {}
        out.append(row)
        seen.add(item["item_key"])

    for item_key, row in existing.items():
        if item_key not in seen:
            db.delete(row)

    db.flush()
    ordered = sorted(out, key=lambda row: row.item_order)
    return ordered


def ensure_canonical_items(db: Session, assessment: Assessments) -> list[AssessmentItem]:
    existing = (
        db.query(AssessmentItem)
        .filter(AssessmentItem.assessment_id == assessment.id)
        .order_by(AssessmentItem.item_order, AssessmentItem.id)
        .all()
    )
    if existing:
        return existing

    definition = get_assessment_definition(assessment.assessment_type_code)
    if definition.code == "leadership_core":
        items = []
        for index, template in enumerate(LEADERSHIP_ITEM_TEMPLATES, start=1):
            question_text = getattr(assessment, template.key, None)
            if question_text and str(question_text).strip():
                items.append(
                    {
                        "item_key": template.key,
                        "display_label": template.label,
                        "item_order": index,
                        "item_kind": template.item_kind,
                        "prompt_text": str(question_text).strip(),
                        "item_meta": template.meta or {},
                    }
                )
        if items:
            return sync_assessment_items(db, assessment, items)

    # For non-legacy assessments or empty leadership rows, fall back to type defaults.
    items = build_assessment_items(definition, job=assessment.job_requirements)
    return sync_assessment_items(db, assessment, items)


def get_assessment_items(db: Session, assessment: Assessments) -> list[AssessmentItem]:
    return ensure_canonical_items(db, assessment)


def get_assessment_item_payloads(db: Session, assessment: Assessments) -> list[dict[str, Any]]:
    items = get_assessment_items(db, assessment)
    return [
        {
            "item_key": item.item_key,
            "display_label": item.display_label,
            "item_order": item.item_order,
            "item_kind": item.item_kind,
            "prompt_text": item.prompt_text,
            "item_meta": item.item_meta or {},
        }
        for item in items
    ]


def save_assessment_answer(
    assessment_id: int,
    item_key: str,
    answer_text: str,
    *,
    question_text: str | None = None,
    answer_meta: dict | None = None,
) -> bool:
    db = SessionLocal()
    try:
        assessment = db.get(Assessments, assessment_id)
        if assessment is None:
            return False

        items = get_assessment_items(db, assessment)
        item = next((row for row in items if row.item_key == item_key), None)
        row = (
            db.query(AssessmentAnswer)
            .filter(
                AssessmentAnswer.assessment_id == assessment_id,
                AssessmentAnswer.item_key == item_key,
            )
            .first()
        )
        clean_answer = (answer_text or "").strip() or None
        if row is None:
            row = AssessmentAnswer(
                assessment_id=assessment_id,
                assessment_item_id=item.id if item else None,
                item_key=item_key,
                question_text=question_text or (item.prompt_text if item else None),
                answer_text=clean_answer,
                answer_meta=answer_meta or {},
            )
            db.add(row)
        else:
            row.assessment_item_id = item.id if item else row.assessment_item_id
            row.question_text = question_text or row.question_text or (item.prompt_text if item else None)
            row.answer_text = clean_answer
            row.answer_meta = answer_meta or row.answer_meta or {}

        # Compatibility bridge for the original leadership assessment schema.
        legacy_field = LEGACY_RESPONSE_FIELD_BY_ITEM_KEY.get(item_key)
        if legacy_field:
            legacy_row = ensure_responses_row(db, assessment_id)
            if hasattr(legacy_row, legacy_field):
                setattr(legacy_row, legacy_field, clean_answer)
        else:
            ensure_responses_row(db, assessment_id)

        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


def count_saved_answers(assessment_id: int, ordered_item_keys: list[str]) -> int:
    db = SessionLocal()
    try:
        canonical_answers = {
            row.item_key: row.answer_text
            for row in db.query(AssessmentAnswer)
            .filter(AssessmentAnswer.assessment_id == assessment_id)
            .all()
        }
        legacy_row = db.query(Responses).filter(Responses.assessment_id == assessment_id).first()
        count = 0
        for item_key in ordered_item_keys:
            answer_text = canonical_answers.get(item_key)
            if not answer_text:
                legacy_field = LEGACY_RESPONSE_FIELD_BY_ITEM_KEY.get(item_key)
                if legacy_row is not None and legacy_field:
                    answer_text = getattr(legacy_row, legacy_field, None)
            if answer_text and str(answer_text).strip():
                count += 1
                continue
            break
        return count
    finally:
        db.close()


def iter_assessment_answers(db: Session, assessment: Assessments) -> list[dict[str, Any]]:
    items = get_assessment_items(db, assessment)
    answer_rows = {
        row.item_key: row
        for row in db.query(AssessmentAnswer)
        .filter(AssessmentAnswer.assessment_id == assessment.id)
        .all()
    }
    legacy_row = db.query(Responses).filter(Responses.assessment_id == assessment.id).first()
    payloads: list[dict[str, Any]] = []
    for item in items:
        answer_text = None
        answer = answer_rows.get(item.item_key)
        if answer and answer.answer_text:
            answer_text = answer.answer_text.strip()
        if not answer_text:
            legacy_field = LEGACY_RESPONSE_FIELD_BY_ITEM_KEY.get(item.item_key)
            if legacy_row is not None and legacy_field:
                raw = getattr(legacy_row, legacy_field, None)
                if raw and str(raw).strip():
                    answer_text = str(raw).strip()
        payloads.append(
            {
                "item_key": item.item_key,
                "display_label": item.display_label,
                "question_text": item.prompt_text,
                "answer_text": answer_text or "",
                "item_order": item.item_order,
            }
        )
    return payloads
