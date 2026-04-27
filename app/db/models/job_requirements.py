from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.index import Base


class JobRequirements(Base):
    __tablename__ = "job_requirements"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    requirement = Column(Text, nullable=False)
    skill = Column(Text, nullable=True)
    soft_skill = Column(Text, nullable=True)
    experience = Column(Text, nullable=True)
    education = Column(Text, nullable=True)
    certification = Column(Text, nullable=True)
    responsibility = Column(Text, nullable=True)
    language = Column(Text, nullable=True)
    industry_experience = Column(Text, nullable=True)
    role_experience = Column(Text, nullable=True)
    location = Column(Text, nullable=True)
    availability = Column(Text, nullable=True)
    work_authorization = Column(Text, nullable=True)
    seniority_level = Column(Text, nullable=True)
    culture_fit = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    assessments          = relationship("Assessments", back_populates="job_requirements")
    analyses             = relationship("Analysis", back_populates="job_requirements")
    requirement_profiles = relationship("JobRequirementProfile", back_populates="job_requirements", cascade="all, delete-orphan")
