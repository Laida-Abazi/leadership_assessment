from os import getenv

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

DATABASE_URL = getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/leadership_assessment"
)

# Cloud SQL is on a tiny tier (db-f1-micro, ~25 max_connections) shared across
# multiple independently-scaling Cloud Run instances and Celery workers.
# Keep each process's footprint small so it doesn't monopolize the pool, and
# recycle connections periodically since Cloud SQL can drop idle ones the
# pool isn't aware of.
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=3,
    max_overflow=2,
    pool_recycle=300,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
