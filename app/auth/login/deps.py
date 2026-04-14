import os

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.login.service import decode_access_token
from app.db import get_db
from app.db.models import User

ACCESS_TOKEN_COOKIE_NAME = os.getenv("ACCESS_TOKEN_COOKIE_NAME", "access_token")
security = HTTPBearer(auto_error=False)


def get_current_user_id(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> int:
    """Extract auth token from Authorization header first, then cookie fallback."""
    token = None
    if credentials and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    if not token:
        token = request.cookies.get(ACCESS_TOKEN_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return decode_access_token(token)


def require_authenticated_user(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before using this application.",
        )
    return user
