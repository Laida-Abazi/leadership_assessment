from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.index import Base


class AssessmentCandidate(Base):
    __tablename__ = "assessment_candidates"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    access_link_id = Column(
        Integer,
        ForeignKey("assessment_access_links.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    first_name = Column(String(120), nullable=False)
    last_name = Column(String(120), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    assessment_type_code = Column(String(64), nullable=False)
    analysis_snapshot = Column(Text, nullable=True)
    prediction_snapshot = Column(Text, nullable=True)
    fit_score = Column(Float, nullable=True)
    confidence_score = Column(Float, nullable=True)
    risk_flags = Column(JSONB, nullable=True)
    link_token = Column(String(255), nullable=False)
    link_created_at = Column(DateTime(timezone=True), nullable=True)
    link_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_result_sync_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    assessment = relationship("Assessments", back_populates="candidates")
    access_link = relationship("AssessmentAccessLink", back_populates="candidate")
    answers = relationship("AssessmentAnswer", back_populates="candidate", cascade="all, delete-orphan")
    responses = relationship("Responses", back_populates="candidate", uselist=False, cascade="all, delete-orphan")
    response_segments = relationship("ResponseSegment", back_populates="candidate", cascade="all, delete-orphan")
    response_signals = relationship("ResponseSignal", back_populates="candidate", cascade="all, delete-orphan")
    analyses = relationship("Analysis", back_populates="candidate", cascade="all, delete-orphan")
    assessment_result = relationship("AssessmentResult", back_populates="candidate", uselist=False, cascade="all, delete-orphan")
