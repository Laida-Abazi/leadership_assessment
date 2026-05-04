from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.index import Base


class ResponseSegment(Base):
    __tablename__ = "response_segments"

    id             = Column(Integer, primary_key=True, index=True)
    assessment_id  = Column(Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False)
    candidate_id   = Column(Integer, ForeignKey("assessment_candidates.id", ondelete="CASCADE"), nullable=True, index=True)
    response_type  = Column(String(64), nullable=False)
    question_id    = Column(String(128), nullable=True)
    segment_text   = Column(Text, nullable=False)
    sequence_order = Column(Integer, nullable=False)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())

    assessment = relationship("Assessments", back_populates="response_segments")
    candidate  = relationship("AssessmentCandidate", back_populates="response_segments")
    signals    = relationship("ResponseSignal", back_populates="segment", cascade="all, delete-orphan")
