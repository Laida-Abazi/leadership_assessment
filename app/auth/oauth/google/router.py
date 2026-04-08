"""
Google OAuth routes using Authlib (OpenID Connect).
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from oauth.google.config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_METADATA_URL,
    GOOGLE_SCOPES,
)
from oauth.google.service import get_or_create_google_user, issue_tokens_for_user
from login.schemas import LoginResponse, TokenPayload, UserBrief

router = APIRouter()

# OAuth client (Authlib) – uses server_metadata_url for OpenID Connect
oauth = OAuth()
oauth.register(
    name="google",
    server_metadata_url=GOOGLE_METADATA_URL,
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    client_kwargs={"scope": GOOGLE_SCOPES},
)


@router.get(
    "/google_login",
    summary="Redirect to Google OAuth",
    description=(
        "Initiates Google OAuth flow; returns 302 redirect to Google's sign-in page. "
        "**Do not call this from Swagger 'Execute' or via fetch/XHR** — it will fail (CORS). "
        "Open this URL in the browser instead: `GET /auth/google_login` (e.g. http://localhost:8000/auth/google_login)."
    ),
    response_class=RedirectResponse,
)
async def google_login(request: Request):
    """Redirect user to Google OAuth consent screen."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured (missing GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET)",
        )
    # Redirect URI is built from the callback route so it works behind proxies and in dev.
    redirect_uri = request.url_for("auth")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get(
    "/google_auth",
    name="auth",
    summary="Google OAuth callback",
    description="Handles callback from Google; finds or creates user, returns JWT and user (same as login).",
    response_model=LoginResponse,
)
async def google_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Handles the OAuth callback from Google.
    Finds or creates user in DB, issues JWT, returns tokens + user (same shape as POST /auth/login).
    """
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"OAuth error: invalid or expired authorization (e.g. missing or invalid code/state). {str(e)}",
        )

    if not token:
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid OAuth token (e.g. user denied consent or session expired).",
        )

    userinfo = token.get("userinfo")
    if not userinfo:
        raise HTTPException(
            status_code=400,
            detail="Could not retrieve user info from Google.",
        )

    email_verified = userinfo.get("email_verified", False)
    if not email_verified:
        raise HTTPException(
            status_code=403,
            detail="Google account email is not verified. Please verify your email with Google and try again.",
        )

    email = userinfo.get("email")
    if not email:
        raise HTTPException(
            status_code=400,
            detail="Google did not provide an email address.",
        )

    client_ip = request.client.host if request.client else None
    user = await get_or_create_google_user(
        db,
        email,
        name=userinfo.get("name"),
        given_name=userinfo.get("given_name"),
        family_name=userinfo.get("family_name"),
        picture=userinfo.get("picture"),
        client_ip=client_ip,
    )
    refresh_token = await issue_tokens_for_user(db, user, client_ip)

    # Session layer: create session then issue JWT with user_id + session_id (same as email/password and magic link)
    from session.auth import create_access_token
    from session.sessions import create_session

    session_data = await create_session(user.id, request, db)
    access_token, expires_in = create_access_token(
        data={"user_id": str(user.id), "session_id": str(session_data["id"])}
    )

    return LoginResponse(
        token=TokenPayload(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        ),
        user=UserBrief.model_validate(user),
    )
