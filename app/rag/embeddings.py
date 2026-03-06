"""RAG service: store and retrieve requirements + assessment questions as embeddings.

Use this module to:
- Index an assessment: embed its job requirements and generated questions into the vector table.
- Retrieve context: given a query (e.g. current conversation turn), fetch the most relevant
  requirements and questions so the agent stays context-aware.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.embeddings import (
    AssessmentContextEmbedding,
    EMBEDDING_DIM,
)
from app.db.models.assessments import Assessments
from app.db.models.job_requirements import JobRequirements

# Content types for content_type column; must match DB/model usage.
CONTENT_TYPE_REQUIREMENT = "requirement"
QUESTION_CONTENT_TYPES = [
    "behavioral_question",
    "competency_based_question",
    "situational_question",
    "panel_question",
    "business_case_question",
    "live_simulation_question",
    "psychometric_question",
    "structured_reference_question",
    "culture_alignment_question",
    "integrity_ethics_question",
]
# Map assessment column name -> content_type
ASSESSMENT_COLUMN_TO_CONTENT_TYPE = {
    "behavioral_question": "behavioral_question",
    "competency_based_question": "competency_based_question",
    "situational_question": "situational_question",
    "panel_question": "panel_question",
    "business_case_question": "business_case_question",
    "live_simulation_question": "live_simulation_question",
    "psychometric_question": "psychometric_question",
    "structured_reference_question": "structured_reference_question",
    "culture_alignment_question": "culture_alignment_question",
    "integrity_ethics_question": "integrity_ethics_question",
}


def get_embedding(text: str) -> List[float]:
    """Produce embedding for a single text using OpenAI. Caller must set OPENAI_API_KEY."""
    from openai import OpenAI

    client = OpenAI()
    resp = client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return resp.data[0].embedding


def index_assessment(
    db: Session,
    assessment: Assessments,
    job_requirements: JobRequirements,
    *,
    embed_fn: Optional[Callable[[str], List[float]]] = None,
) -> int:
    """Embed and store all context for an assessment: requirements + non-null questions.

    - Embeds each requirement line (requirement text) as content_type='requirement'.
    - For each non-null question column on the assessment, embeds that question with
      the corresponding content_type.

    Uses embed_fn(text) -> list[float] if provided; otherwise uses get_embedding (OpenAI).
    Returns the number of embedding rows inserted.
    """
    if embed_fn is None:
        embed_fn = get_embedding

    to_insert: List[dict] = []

    # Requirements: one embedding per requirement text (single requirement field)
    if job_requirements.requirement:
        req_text = job_requirements.requirement.strip()
        if req_text:
            to_insert.append({
                "content_type": CONTENT_TYPE_REQUIREMENT,
                "content": req_text,
            })

    # Optional: embed other job requirement fields (skill, soft_skill, etc.) for richer context
    for attr in ("skill", "soft_skill", "experience", "education", "certification",
                 "responsibility", "culture_fit"):
        val = getattr(job_requirements, attr, None)
        if val and isinstance(val, str) and val.strip():
            to_insert.append({
                "content_type": CONTENT_TYPE_REQUIREMENT,
                "content": val.strip(),
            })

    # Questions from assessment
    for col, content_type in ASSESSMENT_COLUMN_TO_CONTENT_TYPE.items():
        question_text = getattr(assessment, col, None)
        if question_text and isinstance(question_text, str) and question_text.strip():
            to_insert.append({
                "content_type": content_type,
                "content": question_text.strip(),
            })

    if not to_insert:
        return 0

    # Batch embed and insert
    count = 0
    for item in to_insert:
        vec = embed_fn(item["content"])
        if len(vec) != EMBEDDING_DIM:
            raise ValueError(
                f"embed_fn returned dimension {len(vec)}, expected {EMBEDDING_DIM}"
            )
        row = AssessmentContextEmbedding(
            assessment_id=assessment.id,
            job_requirements_id=job_requirements.id,
            content_type=item["content_type"],
            content=item["content"],
            embedding=vec,
        )
        db.add(row)
        count += 1
    db.commit()
    return count


def retrieve_context(
    db: Session,
    query_embedding: Sequence[float],
    *,
    assessment_id: Optional[int] = None,
    job_requirements_id: Optional[int] = None,
    content_types: Optional[List[str]] = None,
    limit: int = 20,
) -> List[dict]:
    """Return the most relevant stored chunks for a query embedding.

    Each result is a dict with keys: content_type, content, (optional) assessment_id, job_requirements_id.

    - Filter by assessment_id and/or job_requirements_id when provided.
    - content_types: if provided, only return rows with content_type in this list.
    - limit: max number of results (default 20).
    """
    # pgvector: order by cosine distance (lower = more similar)
    distance = AssessmentContextEmbedding.embedding.cosine_distance(query_embedding)
    q = (
        select(
            AssessmentContextEmbedding.content_type,
            AssessmentContextEmbedding.content,
            AssessmentContextEmbedding.assessment_id,
            AssessmentContextEmbedding.job_requirements_id,
        )
        .where(True)
    )
    if assessment_id is not None:
        q = q.where(AssessmentContextEmbedding.assessment_id == assessment_id)
    if job_requirements_id is not None:
        q = q.where(AssessmentContextEmbedding.job_requirements_id == job_requirements_id)
    if content_types:
        q = q.where(AssessmentContextEmbedding.content_type.in_(content_types))
    q = q.order_by(distance).limit(limit)

    rows = db.execute(q).all()
    return [
        {
            "content_type": r.content_type,
            "content": r.content,
            "assessment_id": r.assessment_id,
            "job_requirements_id": r.job_requirements_id,
        }
        for r in rows
    ]


def get_context_for_agent(
    db: Session,
    query_text: str,
    *,
    assessment_id: Optional[int] = None,
    job_requirements_id: Optional[int] = None,
    limit: int = 15,
) -> List[dict]:
    """Convenience: embed the query text and return retrieved context for the agent.

    Use this from the conversation agent: pass the current user message (or last N messages
    concatenated) as query_text, and optionally scope by assessment_id / job_requirements_id.
    """
    query_embedding = get_embedding(query_text)
    return retrieve_context(
        db,
        query_embedding,
        assessment_id=assessment_id,
        job_requirements_id=job_requirements_id,
        limit=limit,
    )
