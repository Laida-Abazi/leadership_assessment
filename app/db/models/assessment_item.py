from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.index import Base


class AssessmentItem(Base):
    __tablename__ = "assessment_items"
    __table_args__ = (
        UniqueConstraint("assessment_id", "item_key", name="uq_assessment_items_assessment_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    item_key = Column(String(128), nullable=False, index=True)
    display_label = Column(String(128), nullable=False)
    item_order = Column(Integer, nullable=False)
    item_kind = Column(String(64), nullable=False, default="open_text")
    prompt_text = Column(Text, nullable=False)
    item_meta = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    assessment = relationship("Assessments", back_populates="assessment_items")
    answers = relationship("AssessmentAnswer", back_populates="assessment_item")
