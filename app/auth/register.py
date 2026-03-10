import secrets
from datetime import datetime, timedelta, timezone
from os import getenv

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.db.models import User
from app.auth.email import send_verification_email, send_reset_password_email

router = APIRouter(prefix="/auth", tags=["auth"])

JWT_SECRET = getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = int(getenv("JWT_EXPIRATION_HOURS", "24"))


class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    surname: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class RegisterResponse(BaseModel):
    id: int
    email: str
    name: str
    surname: str
    message: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8, max_length=128)


def hash_password(password: str) -> str:
    pw_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))


def create_access_token(user_id: int, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _build_url(request: Request, path: str) -> str:
    base = getenv("APP_URL", str(request.base_url).rstrip("/"))
    return f"{base}{path}"


@router.post("/register", response_model=RegisterResponse, status_code=201)
def register(request: RegisterRequest, req: Request, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == request.email).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail="A user with this email address already exists.",
        )

    token = secrets.token_urlsafe(32)
    user = User(
        name=request.name.strip(),
        surname=request.surname.strip(),
        email=request.email.lower(),
        password=hash_password(request.password),
        is_verified=False,
        verification_token=token,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    verification_url = _build_url(req, f"/auth/verify/{token}")
    send_verification_email(user.email, user.name, verification_url)

    return RegisterResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        surname=user.surname,
        message="Account created. Please check your email to verify your account.",
    )


@router.get("/verify/{token}")
def verify_email(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.verification_token == token).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link.")

    if user.is_verified:
        raise HTTPException(status_code=400, detail="Account is already verified.")

    user.is_verified = True
    user.verification_token = None
    db.commit()

    return {"success": True}


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == request.email.lower()).first()
    if not user or not verify_password(request.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Please verify your email before logging in.")

    token = create_access_token(user.id, user.email)
    return LoginResponse(
        access_token=token,
        user={
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "surname": user.surname,
        },
    )


@router.post("/forgot-password")
def forgot_password(request: ForgotPasswordRequest, req: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == request.email.lower()).first()
    if not user:
        return {"success": True, "message": "If that email exists, a reset link has been sent."}

    token = secrets.token_urlsafe(32)
    user.reset_password_token = token
    db.commit()

    reset_url = _build_url(req, f"/auth/reset-password/{token}")
    send_reset_password_email(user.email, user.name, reset_url)

    return {"success": True, "message": "If that email exists, a reset link has been sent."}


@router.post("/reset-password")
def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.reset_password_token == request.token).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token.")

    user.password = hash_password(request.new_password)
    user.reset_password_token = None
    db.commit()

    return {"success": True, "message": "Password has been reset successfully."}
