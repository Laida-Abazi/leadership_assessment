"""
Google OAuth: find or create user and issue tokens.
"""
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from signup.service import hash_password
from login.service import create_refresh_token


def _username_base_from_email(email: str) -> str:
    """Generate a username base from email (local part sanitized + random suffix)."""
    local = (email or "").split("@")[0]
    base = re.sub(r"[^a-zA-Z0-9]", "_", local)[:25] or "user"
    base = base.strip("_") or "user"
    return f"{base}_{secrets.token_hex(3)}"


async def _find_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_or_create_google_user(
    db: AsyncSession,
    email: str,
    *,
    name: str | None = None,
    given_name: str | None = None,
    family_name: str | None = None,
    picture: str | None = None,
    client_ip: str | None = None,
) -> User:
    """
    Find user by email or create one for Google OAuth.
    New users get a generated username and a random (unusable) password.
    """
    user = await _find_user_by_email(db, email)
    if user is not None:
        # Update profile from Google and last login
        if given_name is not None:
            user.first_name = given_name
        if family_name is not None:
            user.last_name = family_name
        if picture is not None:
            user.profile_image_url = picture
        user.last_login_at = datetime.now(timezone.utc)
        user.last_login_ip = client_ip
        # Ensure OAuth users are treated as verified
        if not user.is_verified:
            user.is_verified = True
            user.email_verified_at = datetime.now(timezone.utc)
            user.email_verification_token = None
        await db.flush()
        await db.refresh(user)
        return user

    # Create new user: need unique username and a placeholder password
    username = _username_base_from_email(email)
    while True:
        existing = await db.execute(select(User).where(User.username == username))
        if existing.scalar_one_or_none() is None:
            break
        username = f"{username}_{secrets.token_hex(2)}"

    user = User(
        email=email,
        username=username,
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        first_name=given_name or (name and name.split(None, 1)[0]),
        last_name=family_name or (name and name.split(None, 1)[-1] if name and name.count(" ") else None),
        profile_image_url=picture,
        is_verified=True,
        email_verified_at=datetime.now(timezone.utc),
        last_login_at=datetime.now(timezone.utc),
        last_login_ip=client_ip,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def issue_tokens_for_user(
    db: AsyncSession,
    user: User,
    client_ip: str | None = None,
) -> str:
    """Update user refresh token and last login; return refresh_token. Access token is issued by caller via session layer."""
    refresh_token, refresh_expires = create_refresh_token()
    user.refresh_token = refresh_token
    user.refresh_token_expires = refresh_expires
    user.last_login_at = datetime.now(timezone.utc)
    user.last_login_ip = client_ip
    await db.flush()
    return refresh_token
