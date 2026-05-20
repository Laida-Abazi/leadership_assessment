import contextlib
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# This module is ONLY for Celery workers. FastAPI routes use the async session
# from app/db.py or app/database.py. Never import this in FastAPI route handlers.
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required for Celery workers.")

SYNC_DATABASE_URL = (
    DATABASE_URL.replace("+asyncpg", "+psycopg2")
    if "+asyncpg" in DATABASE_URL
    else DATABASE_URL
)

sync_engine = create_engine(
    SYNC_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autocommit=False,
    autoflush=False,
)


@contextlib.contextmanager
def get_sync_db():
    db: Session = SyncSessionLocal()
    try:
        yield db
    finally:
        db.close()
