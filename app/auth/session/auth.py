"""
JWT creation/decoding and FastAPI dependency for session-aware authentication.
Never trust JWT alone — get_current_session always validates the session in the store.
"""
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.database import get_db
from models.session import Session
from models.user import User

logger = logging.getLogger(__name__)

JWT_SECRET_ENV_KEY = "JWT_SECRET_KEY"
# Stable fallback avoids invalidating tokens on server reload when env is missing.
DEFAULT_DEV_SECRET = "leadership-assessment-dev-jwt-secret-change-me"
SECRET_KEY = os.getenv(JWT_SECRET_ENV_KEY, DEFAULT_DEV_SECRET)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

security = HTTPBearer(auto_error=True)


def create_access_token(data: dict) -> tuple[str, int]:
    """
    Create a JWT containing user_id and session_id.
    Used after ANY successful login (email/password, OAuth, magic link).
    """
    user_id = data.get("user_id")
    session_id = data.get("session_id")
    if not user_id or not session_id:
        raise ValueError("data must contain 'user_id' and 'session_id'")
    expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    expire = datetime.now(timezone.utc) + expires_delta
    payload = {
        "sub": str(user_id),
        "session_id": str(session_id),
        "exp": expire,
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict:
    """
    Decode and validate JWT. Returns dict with user_id (uuid) and session_id (uuid).
    Raises HTTPException 401 if invalid or expired.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        session_id = payload.get("session_id")
        if not sub or not session_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user_id or session_id",
            )
        return {
            "user_id": uuid.UUID(sub),
            "session_id": uuid.UUID(session_id),
        }
    except (JWTError, ValueError, TypeError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from e


async def get_current_session(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> tuple[User, Session]:
    """
    FastAPI dependency: extract JWT, validate session in store, return (current_user, current_session).
    Rejects revoked or expired sessions with 401/403.
    """
    token = credentials.credentials
    payload = decode_access_token(token)
    user_id = payload["user_id"]
    session_id = payload["session_id"]

    # Fetch session from store — never trust JWT alone
    result = await db.execute(
        select(Session)
        .where(Session.id == session_id)
        .options(selectinload(Session.user))
    )
    session = result.scalar_one_or_none()
    if session is None:
        logger.warning("Session not found: %s", session_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session not found or invalid",
        )
    if session.revoked:
        logger.info("Rejected revoked session: %s", session_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked",
        )
    if session.is_expired:
        logger.info("Rejected expired session: %s", session_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has expired",
        )
    if session.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session does not belong to user",
        )

    user = session.user
    if user is None:
        result_user = await db.execute(select(User).where(User.id == user_id))
        user = result_user.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    return user, session
