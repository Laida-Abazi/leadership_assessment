from sqlalchemy import Column, Integer, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.db.index import Base


class Analysis(Base):
    __tablename__ = "analysis"

    id = Column(Integer, primary_key=True, index=True)
    job_requirements_id = Column(Integer, ForeignKey("job_requirements.id"), nullable=False)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False)
    responses_id = Column(Integer, ForeignKey("responses.id"), nullable=False)
    analysis = Column(Text, nullable=False)

    job_requirements = relationship("JobRequirements", back_populates="analyses")
    assessment = relationship("Assessments", back_populates="analyses")
    responses = relationship("Responses", back_populates="analyses")
    predictions = relationship("Predictions", back_populates="analysis", uselist=False)
