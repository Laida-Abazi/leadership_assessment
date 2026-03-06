from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from app.db.index import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    surname = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    password = Column(String, nullable=False)

    assessments = relationship("Assessments", back_populates="user")
