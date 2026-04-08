import os
import re
import secrets
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mail.email_utils import send_email
from models.user import User
from signup.service import hash_password

# In-memory token store for demo. Replace with database table in production.
_magic_tokens: dict[str, dict[str, Any]] = {}

MAGIC_LINK_EXPIRE_MINUTES = 12
MAGIC_LINK_BASE_URL = os.getenv("MAGIC_LINK_BASE_URL", "http://localhost:8000")


def create_token() -> str:
    """Generate a secure one-time token using secrets.token_urlsafe(32)."""
    return secrets.token_urlsafe(32)


def store_token(token: str, email: str) -> None:
    """Store token with email, expiration timestamp, and used=False."""
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=MAGIC_LINK_EXPIRE_MINUTES)
    _magic_tokens[token] = {
        "email": email,
        "expires_at": expires_at,
        "used": False,
    }


def verify_token(token: str) -> str:
    """
    Verify token: exists, not expired, not used.
    Returns the email associated with the token.
    Raises HTTPException for invalid, expired, or already-used tokens.
    """
    if not token or token not in _magic_tokens:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or missing token.",
        )

    data = _magic_tokens[token]

    if data["used"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This login link has already been used. Please request a new one.",
        )

    if datetime.now(timezone.utc) > data["expires_at"]:
        # Optionally remove expired token to avoid reuse
        del _magic_tokens[token]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This login link has expired. Please request a new one.",
        )

    return data["email"]


def mark_token_used(token: str) -> None:
    """Mark token as used so it cannot be reused."""
    if token in _magic_tokens:
        _magic_tokens[token]["used"] = True


async def send_magic_link_email(email: str, token: str) -> None:
    """Send email containing the magic login link."""
    login_url = f"{MAGIC_LINK_BASE_URL.rstrip('/')}/auth/magic_link/magic-login?token={token}"
    subject = "Your login link"
    body = (
        "Click the link below to sign in. This link is valid for "
        f"{MAGIC_LINK_EXPIRE_MINUTES} minutes and can only be used once.\n\n"
        f"{login_url}\n\n"
        "If you did not request this email, you can safely ignore it."
    )
    await send_email(email, subject, body)


def _sanitize_username(email: str) -> str:
    """Derive a safe username from email (local part, alphanumeric + underscore)."""
    local = email.split("@")[0]
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", local)[:80]
    return safe or "user"


async def get_or_create_user(db: AsyncSession, email: str) -> User:
    """
    Get user by email, or create one if not exist (optional enhancement).
    New users get a placeholder password so they can only log in via magic link.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    base_username = _sanitize_username(email)
    username = base_username
    suffix = 0
    while True:
        result = await db.execute(select(User).where(User.username == username))
        if result.scalar_one_or_none() is None:
            break
        suffix += 1
        username = f"{base_username}_{suffix}"[:100]

    user = User(
        email=email,
        username=username,
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def request_magic_link(email: str) -> str:
    """
    Generate token, store it, send magic link email.
    Returns a generic message (same whether or not email exists) to avoid enumeration.
    """
    token = create_token()
    store_token(token, email)
    await send_magic_link_email(email, token)
    return (
        "If an account with that email exists (or you just signed up), "
        "we've sent you a login link. Check your inbox."
    )


async def consume_magic_link(db: AsyncSession, token: str) -> str:
    """
    Verify token, mark as used, (optionally) get or create user, return email.
    """
    email = verify_token(token)
    mark_token_used(token)
    # Optional: ensure user exists for session/JWT later
    await get_or_create_user(db, email)
    return email
