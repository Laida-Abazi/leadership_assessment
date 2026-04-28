from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.index import Base


class AssessmentAccessLink(Base):
    __tablename__ = "assessment_access_links"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    token_salt = Column(String(64), nullable=False)
    candidate_email = Column(String(255), nullable=True)
    issued_reason = Column(Text, nullable=True)
    max_uses = Column(Integer, nullable=False, default=1, server_default="1")
    use_count = Column(Integer, nullable=False, default=0, server_default="0")
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    used_by_fingerprint = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    assessment = relationship("Assessments", back_populates="access_links")
    created_by = relationship("User", back_populates="issued_interview_links")
