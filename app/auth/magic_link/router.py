from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from magic_link.schemas import (
    MagicLinkLoginRequest,
    MagicLinkLoginResponse,
    MagicLinkSuccessResponse,
)
from magic_link.service import consume_magic_link, request_magic_link
from models.user import User
from session.auth import create_access_token
from session.sessions import create_session

router = APIRouter()


@router.post(
    "/login-request",
    response_model=MagicLinkLoginResponse,
    summary="Request a magic link",
    description="Submit your email to receive a one-time login link.",
)
async def login_request(payload: MagicLinkLoginRequest) -> MagicLinkLoginResponse:
    message = await request_magic_link(payload.email)
    return MagicLinkLoginResponse(message=message)


@router.get(
    "/magic-login",
    response_model=MagicLinkSuccessResponse,
    summary="Log in with magic link token",
    description="Verify the token from the email link and authenticate the user.",
)
async def magic_login(
    request: Request,
    token: str = Query(..., description="One-time token from the magic link email"),
    db: AsyncSession = Depends(get_db),
) -> MagicLinkSuccessResponse:
    email = await consume_magic_link(db, token)
    # Get user (consume_magic_link already called get_or_create_user)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=500, detail="User not found after magic link")
    # Session layer: same as email/password and Google OAuth
    session_data = await create_session(user.id, request, db)
    access_token, expires_in = create_access_token(
        data={"user_id": str(user.id), "session_id": str(session_data["id"])}
    )
    return MagicLinkSuccessResponse(
        email=email,
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
    )
