from sqlalchemy import Column, Integer, ForeignKey, Text, DateTime
from sqlalchemy.orm import relationship
from app.db.index import Base
from sqlalchemy.sql import func


class Responses(Base):
    __tablename__ = "responses"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False)
    candidate_id = Column(Integer, ForeignKey("assessment_candidates.id", ondelete="CASCADE"), nullable=True, index=True)
    behavioral_response = Column(Text, nullable=True)
    competency_based_response = Column(Text, nullable=True)
    situational_response = Column(Text, nullable=True)
    panel_response = Column(Text, nullable=True)
    business_case_response = Column(Text, nullable=True)
    live_simulation_response = Column(Text, nullable=True)
    psychometric_response = Column(Text, nullable=True)
    structured_reference_response = Column(Text, nullable=True)
    culture_alignment_response = Column(Text, nullable=True)
    integrity_ethics_response = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    assessment = relationship("Assessments", back_populates="responses")
    candidate = relationship("AssessmentCandidate", back_populates="responses")
    analyses = relationship("Analysis", back_populates="responses")
