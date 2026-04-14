import os
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.signup.service import verify_password
from app.db.models import User

JWT_SECRET_ENV_KEY = "JWT_SECRET_KEY"
# Stable fallback avoids invalidating tokens on server reload when env is missing.
DEFAULT_DEV_SECRET = "leadership-assessment-dev-jwt-secret-change-me"
SECRET_KEY = os.getenv(JWT_SECRET_ENV_KEY, DEFAULT_DEV_SECRET)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 3600
REFRESH_TOKEN_EXPIRE_DAYS = 7
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def decode_access_token(token: str) -> int:
    """Decode and validate access token; return user id. Raises HTTPException 401 if invalid."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        return int(sub)
    except (JWTError, ValueError, TypeError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from e


def create_access_token(user: User) -> str:
    expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    expire = datetime.now(timezone.utc) + expires_delta
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _get_user_by_email(
    db: Session, identifier: str
) -> User | None:
    result = db.execute(select(User).where(User.email == identifier.lower()))
    return result.scalar_one_or_none()


def authenticate_user(
    db: Session,
    email: str,
    password: str,
    client_ip: str | None = None,  # kept for router compatibility
) -> User:
    user = _get_user_by_email(db, email)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before logging in.",
        )

    if not verify_password(password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    return user
