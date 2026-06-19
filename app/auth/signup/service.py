import secrets
from os import getenv

import bcrypt
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.email import send_verification_email
from app.auth.signup.schemas import SignupRequest
from app.db.models import User

EMAIL_VERIFICATION_ENABLED = getenv("EMAIL_VERIFICATION_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))


def generate_verification_token() -> str:
    return secrets.token_urlsafe(32)


def check_existing_user(db: Session, email: str) -> None:
    result = db.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()
    if existing is None:
        return
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="A user with this email address already exists.",
    )


def create_user(db: Session, payload: SignupRequest, verification_url: str) -> User:
    check_existing_user(db, payload.email.lower())

    token = generate_verification_token() if EMAIL_VERIFICATION_ENABLED else None
    user = User(
        name=payload.name.strip(),
        surname=payload.surname.strip(),
        email=payload.email.lower(),
        password=hash_password(payload.password),
        is_verified=not EMAIL_VERIFICATION_ENABLED,
        verification_token=token,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    if EMAIL_VERIFICATION_ENABLED and token is not None:
        send_verification_email(user.email, user.name, verification_url.format(token=token))

    return user


def verify_email(db: Session, token: str) -> bool:
    result = db.execute(select(User).where(User.verification_token == token))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification link.",
        )

    if user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account is already verified.",
        )

    user.is_verified = True
    user.verification_token = None
    db.commit()

    return True
