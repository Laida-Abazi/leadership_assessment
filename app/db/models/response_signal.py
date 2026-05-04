from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.index import Base


class ResponseSignal(Base):
    __tablename__ = "response_signals"

    id                  = Column(Integer, primary_key=True, index=True)
    response_segment_id = Column(Integer, ForeignKey("response_segments.id", ondelete="CASCADE"), nullable=True)
    assessment_id       = Column(Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False)
    candidate_id        = Column(Integer, ForeignKey("assessment_candidates.id", ondelete="CASCADE"), nullable=True, index=True)
    response_type       = Column(String(64), nullable=False)
    traits              = Column(JSONB, nullable=True)
    strengths           = Column(JSONB, nullable=True)
    risks               = Column(JSONB, nullable=True)
    confidence          = Column(Float, nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    segment    = relationship("ResponseSegment", back_populates="signals")
    assessment = relationship("Assessments", back_populates="response_signals")
    candidate  = relationship("AssessmentCandidate", back_populates="response_signals")
