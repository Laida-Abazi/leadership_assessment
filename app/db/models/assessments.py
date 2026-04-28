from sqlalchemy import Column, Integer, ForeignKey, Text, String, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from app.db.index import Base
from sqlalchemy.sql import func


class Assessments(Base):
    __tablename__ = "assessments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    job_requirements_id = Column(Integer, ForeignKey("job_requirements.id"), nullable=False)
    assessment_type_code = Column(String(64), nullable=False, default="leadership_core")
    assessment_version = Column(String(32), nullable=False, default="v1")
    assessment_metadata = Column(JSONB, nullable=True)
    behavioral_question = Column(Text, nullable=True)
    competency_based_question = Column(Text, nullable=True)
    situational_question = Column(Text, nullable=True)
    panel_question = Column(Text, nullable=True)
    business_case_question = Column(Text, nullable=True)
    live_simulation_question = Column(Text, nullable=True)
    psychometric_question = Column(Text, nullable=True)
    structured_reference_question = Column(Text, nullable=True)
    culture_alignment_question = Column(Text, nullable=True)
    integrity_ethics_question = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="assessments")
    job_requirements = relationship("JobRequirements", back_populates="assessments")
    responses = relationship("Responses", back_populates="assessment", uselist=False)
    analyses = relationship("Analysis", back_populates="assessment")
    response_segments = relationship("ResponseSegment", back_populates="assessment", cascade="all, delete-orphan")
    response_signals  = relationship("ResponseSignal", back_populates="assessment", cascade="all, delete-orphan")
    assessment_items = relationship("AssessmentItem", back_populates="assessment", cascade="all, delete-orphan")
    assessment_answers = relationship("AssessmentAnswer", back_populates="assessment", cascade="all, delete-orphan")
    assessment_result = relationship("AssessmentResult", back_populates="assessment", uselist=False, cascade="all, delete-orphan")
    access_links = relationship("AssessmentAccessLink", back_populates="assessment", cascade="all, delete-orphan")
