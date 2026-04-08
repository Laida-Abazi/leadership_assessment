"""
Session lifecycle: create, list, revoke. Reusable from any auth flow.
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.session import Session

logger = logging.getLogger(__name__)

SESSION_EXPIRE_DAYS = int(__import__("os").environ.get("SESSION_EXPIRE_DAYS", "7"))


def _user_agent_from_request(request: Request) -> str | None:
    """Extract User-Agent from request headers."""
    return request.headers.get("user-agent") or None


def _ip_from_request(request: Request) -> str | None:
    """Extract client IP (supports X-Forwarded-For when behind proxy)."""
    if request.client:
        return request.client.host
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return None


async def create_session(
    user_id: uuid.UUID | str,
    request: Request,
    db: AsyncSession,
) -> dict:
    """
    Create a new session for the user. Call this from ANY auth flow after successful login:
    - Email/password login
    - Google OAuth callback
    - Magic link verification
    """
    session_id = uuid.uuid4()
    user_agent = _user_agent_from_request(request)
    ip_address = _ip_from_request(request)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=SESSION_EXPIRE_DAYS)

    uid = uuid.UUID(str(user_id)) if isinstance(user_id, str) else user_id
    session = Session(
        id=session_id,
        user_id=uid,
        created_at=now,
        expires_at=expires_at,
        revoked=False,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)

    logger.info(
        "Session created: session_id=%s user_id=%s ip=%s",
        session_id,
        uid,
        ip_address,
    )

    return {
        "id": session.id,
        "user_id": session.user_id,
        "created_at": session.created_at,
        "expires_at": session.expires_at,
        "revoked": session.revoked,
        "user_agent": session.user_agent,
        "ip_address": session.ip_address,
    }


async def get_session_by_id(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> Session | None:
    """Fetch session by id; optionally restrict to a user."""
    q = select(Session).where(Session.id == session_id)
    if user_id is not None:
        q = q.where(Session.user_id == user_id)
    result = await db.execute(q)
    return result.scalar_one_or_none()


async def list_active_sessions_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    current_session_id: uuid.UUID | None = None,
) -> list[Session]:
    """Return all active (non-revoked, non-expired) sessions for the user."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Session)
        .where(Session.user_id == user_id)
        .where(Session.revoked.is_(False))
        .where(Session.expires_at > now)
        .order_by(Session.created_at.desc())
    )
    sessions = list(result.scalars().all())
    # Mark which one is current (for "current" boolean in response)
    if current_session_id:
        for s in sessions:
            setattr(s, "_is_current", s.id == current_session_id)
    else:
        for s in sessions:
            setattr(s, "_is_current", False)
    return sessions


async def revoke_session(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """
    Revoke a session. Returns True if session was found and revoked; False if not found or not owned.
    Ensures user can only revoke their own sessions.
    """
    session = await get_session_by_id(db, session_id, user_id=user_id)
    if session is None:
        return False
    session.revoked = True
    await db.flush()
    logger.info("Session revoked: session_id=%s user_id=%s", session_id, user_id)
    return True


async def revoke_all_sessions_for_user(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Revoke all sessions for the user. Returns count of revoked sessions."""
    result = await db.execute(
        select(Session).where(Session.user_id == user_id).where(Session.revoked.is_(False))
    )
    sessions = result.scalars().all()
    for s in sessions:
        s.revoked = True
    if sessions:
        await db.flush()
        logger.info("Revoked all sessions for user: user_id=%s count=%s", user_id, len(sessions))
    return len(sessions)


async def cleanup_expired_sessions(db: AsyncSession) -> int:
    """
    Optional: delete expired sessions from the database to save space.
    Returns number of rows deleted.
    Sessions are already ignored when expired; this is for housekeeping.
    """
    from sqlalchemy import delete
    now = datetime.now(timezone.utc)
    result = await db.execute(delete(Session).where(Session.expires_at < now))
    return result.rowcount or 0
