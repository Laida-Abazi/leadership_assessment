from sqlalchemy import Column, Integer, ForeignKey, Text, Float, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from app.db.index import Base
from sqlalchemy.sql import func


class Predictions(Base):
    __tablename__ = "predictions"

    id = Column(Integer, primary_key=True, index=True)
    analysis_id = Column(Integer, ForeignKey("analysis.id"), nullable=False)
    prediction = Column(Text, nullable=False)

    # Intelligence pipeline columns (added via migration).
    fit_score        = Column(Float, nullable=True)
    confidence_score = Column(Float, nullable=True)
    risk_flags       = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    analysis = relationship("Analysis", back_populates="predictions")
