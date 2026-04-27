from sqlalchemy import Column, Integer, ForeignKey, Text, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from app.db.index import Base
from sqlalchemy.sql import func



class Analysis(Base):
    __tablename__ = "analysis"

    id = Column(Integer, primary_key=True, index=True)
    job_requirements_id = Column(Integer, ForeignKey("job_requirements.id"), nullable=False)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False)
    responses_id = Column(Integer, ForeignKey("responses.id"), nullable=False)
    # Legacy text blob — kept for backward compatibility; new narrative summary also written here.
    analysis = Column(Text, nullable=False)

    # Intelligence pipeline JSONB columns (added via migration).
    aggregated_traits   = Column(JSONB, nullable=True)
    consistency_scores  = Column(JSONB, nullable=True)
    trait_gaps          = Column(JSONB, nullable=True)
    contradictions      = Column(JSONB, nullable=True)
    behavioral_patterns = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job_requirements = relationship("JobRequirements", back_populates="analyses")
    assessment = relationship("Assessments", back_populates="analyses")
    responses = relationship("Responses", back_populates="analyses")
    predictions = relationship("Predictions", back_populates="analysis", uselist=False)
