import os

from fastapi import Depends, HTTPException, status
from starlette.requests import HTTPConnection
from sqlalchemy.orm import Session

from app.auth.login.service import decode_access_token
from app.db import get_db
from app.db.models import User

ACCESS_TOKEN_COOKIE_NAME = os.getenv("ACCESS_TOKEN_COOKIE_NAME", "access_token")


def get_current_user_id(
    connection: HTTPConnection,
) -> int:
    """Extract auth token from header first, then cookie fallback."""
    token = None
    authorization = connection.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        token = connection.cookies.get(ACCESS_TOKEN_COOKIE_NAME)
    if not token:
        token = connection.query_params.get("access_token")
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
