from sqlalchemy import Column, Integer, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.db.index import Base


class Predictions(Base):
    __tablename__ = "predictions"

    id = Column(Integer, primary_key=True, index=True)
    analysis_id = Column(Integer, ForeignKey("analysis.id"), nullable=False)
    prediction = Column(Text, nullable=False)

    analysis = relationship("Analysis", back_populates="predictions")
