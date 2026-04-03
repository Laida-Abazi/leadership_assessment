from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.index import Base


class AssessmentAnswer(Base):
    __tablename__ = "assessment_answers"
    __table_args__ = (
        UniqueConstraint("assessment_id", "item_key", name="uq_assessment_answers_assessment_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    assessment_item_id = Column(Integer, ForeignKey("assessment_items.id", ondelete="SET NULL"), nullable=True, index=True)
    item_key = Column(String(128), nullable=False, index=True)
    question_text = Column(Text, nullable=True)
    answer_text = Column(Text, nullable=True)
    answer_meta = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    assessment = relationship("Assessments", back_populates="assessment_answers")
    assessment_item = relationship("AssessmentItem", back_populates="answers")
