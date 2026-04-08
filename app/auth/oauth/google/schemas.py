"""
Response schemas for OAuth user info.
"""
from pydantic import BaseModel, EmailStr


class GoogleUserInfo(BaseModel):
    """User info returned after successful Google OAuth."""

    email: EmailStr
    name: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    picture: str | None = None
    email_verified: bool = False
