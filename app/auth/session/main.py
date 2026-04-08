"""
Session management routes: list sessions, revoke session, logout, logout-all, mock-login.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from session.auth import create_access_token, get_current_session
from session.schemas import (
    MockLoginRequest,
    MockLoginResponse,
    SessionListResponse,
    SessionOut,
    TokenPayload,
)
from session.sessions import (
    create_session,
    list_active_sessions_for_user,
    revoke_all_sessions_for_user,
    revoke_session,
)

router = APIRouter()


def _session_to_out(session, current_session_id: uuid.UUID | None) -> SessionOut:
    is_current = current_session_id is not None and session.id == current_session_id
    return SessionOut(
        id=session.id,
        created_at=session.created_at,
        user_agent=session.user_agent,
        ip_address=session.ip_address,
        current=is_current,
    )


# --- A. GET /sessions ---
@router.get(
    "/sessions",
    response_model=SessionListResponse,
    summary="List active sessions",
    description="Return all active (non-revoked, non-expired) sessions for the current user.",
)
async def list_sessions(
    db: Annotated[AsyncSession, Depends(get_db)],
    user_and_session: Annotated[tuple, Depends(get_current_session)],
):
    user, current_session = user_and_session
    sessions = await list_active_sessions_for_user(
        db,
        user.id,
        current_session_id=current_session.id,
    )
    return SessionListResponse(
        sessions=[_session_to_out(s, current_session.id) for s in sessions],
    )


# --- B. DELETE /sessions/{session_id} ---
@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a session",
    description="Revoke a specific session. User can only revoke their own sessions.",
)
async def revoke_one_session(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_and_session: Annotated[tuple, Depends(get_current_session)],
):
    user, _ = user_and_session
    revoked = await revoke_session(db, session_id, user.id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or you do not have permission to revoke it",
        )


# --- C. POST /logout ---
@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out (revoke current session)",
    description="Revoke only the current session.",
)
async def logout_current(
    db: Annotated[AsyncSession, Depends(get_db)],
    user_and_session: Annotated[tuple, Depends(get_current_session)],
):
    user, current_session = user_and_session
    await revoke_session(db, current_session.id, user.id)


# --- D. POST /logout-all ---
@router.post(
    "/logout-all",
    status_code=status.HTTP_200_OK,
    summary="Log out from all devices",
    description="Revoke all sessions for the current user.",
)
async def logout_all(
    db: Annotated[AsyncSession, Depends(get_db)],
    user_and_session: Annotated[tuple, Depends(get_current_session)],
):
    user, _ = user_and_session
    count = await revoke_all_sessions_for_user(db, user.id)
    return {"revoked": count}


# --- Example: POST /mock-login (demonstrates usage for any auth flow) ---
@router.post(
    "/mock-login",
    response_model=MockLoginResponse,
    summary="Mock login (demo)",
    description=(
        "Accept email, create user if not exists, create session, issue JWT. "
        "Use this to test session APIs. Same pattern applies to real login, OAuth, magic link."
    ),
)
async def mock_login(
    payload: MockLoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from sqlalchemy import select
    from models.user import User

    # Get or create user by email (simplified for demo)
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None:
        # Create user if not exists (mock only — no password in real flow would use signup)
        import secrets
        from signup.service import hash_password
        base_username = (payload.email.split("@")[0][:50] or "user").replace(".", "_")
        username = f"{base_username}_{secrets.token_hex(2)}"
        user = User(
            email=payload.email,
            username=username,
            hashed_password=hash_password(secrets.token_urlsafe(32)),
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)

    # --- PLUG-IN POINT: Same steps for ANY auth method ---
    # 1. After successful auth (email/password, Google OAuth callback, or Magic Link verification):
    session_data = await create_session(user.id, request, db)

    # 2. Issue JWT with user_id + session_id (required for get_current_session)
    token, expires_in = create_access_token(
        data={
            "user_id": str(user.id),
            "session_id": str(session_data["id"]),
        }
    )

    # 3. Return token (and optionally user info)
    # --- End of plug-in pattern. Google OAuth would call create_session(...) then create_access_token(...) here.
    # --- Magic Link would do the same after consume_magic_link and get_or_create_user.
    return MockLoginResponse(
        token=TokenPayload(
            access_token=token,
            expires_in=expires_in,
        ),
        user_id=user.id,
        session_id=session_data["id"],
    )
