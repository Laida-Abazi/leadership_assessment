import secrets
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.email import send_reset_password_email
from app.auth.signup.service import hash_password
from app.db.models import User


def _generate_reset_token() -> str:
    return secrets.token_urlsafe(32)


def request_password_reset(db: Session, email: str, reset_url_template: str) -> str:
    generic_msg = (
        "If that email exists, a reset link has been sent."
    )

    result = db.execute(select(User).where(User.email == email.lower()))
    user = result.scalar_one_or_none()

    if user is None:
        return generic_msg

    token = _generate_reset_token()
    user.reset_password_token = token
    db.commit()

    send_reset_password_email(
        user.email,
        user.name,
        reset_url_template.format(token=token),
    )
    return generic_msg


def confirm_password_reset(
    db: Session, token: str, new_password: str
) -> str:
    result = db.execute(
        select(User).where(User.reset_password_token == token)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password-reset token.",
        )

    user.password = hash_password(new_password)
    user.reset_password_token = None
    db.commit()

    return "Password has been reset successfully."
