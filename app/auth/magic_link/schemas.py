from pydantic import BaseModel, EmailStr


class MagicLinkLoginRequest(BaseModel):
    """Request body for requesting a magic link."""

    email: EmailStr


class MagicLinkLoginResponse(BaseModel):
    """Response after requesting a magic link (same message regardless of email existence)."""

    message: str


class MagicLinkSuccessResponse(BaseModel):
    """Response after successful magic-link login."""

    email: str
    access_token: str
    token_type: str = "bearer"
    expires_in: int
