"""
Reusable session management: works with any auth method (Google OAuth, Magic Link, email/password).
"""
from session.auth import (
    create_access_token,
    decode_access_token,
    get_current_session,
)
from session.sessions import create_session

__all__ = [
    "create_access_token",
    "decode_access_token",
    "get_current_session",
    "create_session",
]
