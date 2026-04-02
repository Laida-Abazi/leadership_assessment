from sqlalchemy import Column, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.index import Base


class JobRequirementProfile(Base):
    __tablename__ = "job_requirement_profiles"

    id                  = Column(Integer, primary_key=True, index=True)
    job_requirements_id = Column(Integer, ForeignKey("job_requirements.id", ondelete="CASCADE"), nullable=False)
    trait_expectations  = Column(JSONB, nullable=True)
    weights             = Column(JSONB, nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    job_requirements = relationship("JobRequirements", back_populates="requirement_profiles")
