"""Model for storing requirements and assessment questions as embeddings for RAG.

Used by the conversation agent to retrieve relevant context (job requirements
and generated questions) when talking with the interviewee.
"""
from sqlalchemy import Column, Integer, ForeignKey, Text, String, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

from app.db.index import Base

# OpenAI text-embedding-3-small dimension; use same if you switch to ada-002 (1536)
EMBEDDING_DIM = 1536


class AssessmentContextEmbedding(Base):
    """Stores one embedding per chunk of context: a requirement or an assessment question.

    Each row is the vector representation of either:
    - A job requirement (content_type='requirement', from job_requirements)
    - An assessment question (content_type in question types below, from assessments)

    The agent can filter by assessment_id (or job_requirements_id) and run
    similarity search on the embedding column to pull relevant context.
    """

    __tablename__ = "assessment_context_embeddings"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)
    job_requirements_id = Column(
        Integer, ForeignKey("job_requirements.id"), nullable=False, index=True
    )

    # One of: "requirement" | "behavioral_question" | "competency_based_question" |
    # "situational_question" | "panel_question" | "business_case_question" |
    # "live_simulation_question" | "psychometric_question" |
    # "structured_reference_question" | "culture_alignment_question" | "integrity_ethics_question"
    content_type = Column(String(64), nullable=False, index=True)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(EMBEDDING_DIM), nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    assessment = relationship("Assessments", backref="context_embeddings")
    job_requirements = relationship("JobRequirements", backref="context_embeddings")
