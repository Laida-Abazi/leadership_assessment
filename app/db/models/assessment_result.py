from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.index import Base


class AssessmentResult(Base):
    __tablename__ = "assessment_results"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    shared_result_json = Column(JSONB, nullable=True)
    type_result_json = Column(JSONB, nullable=True)
    narrative = Column(Text, nullable=False, default="")
    fit_score = Column(Float, nullable=True)
    confidence_score = Column(Float, nullable=True)
    risk_flags = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    assessment = relationship("Assessments", back_populates="assessment_result")
