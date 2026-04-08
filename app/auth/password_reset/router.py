from os import getenv

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.password_reset.schemas import (
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetResponse,
)
from app.auth.password_reset.service import confirm_password_reset, request_password_reset
from app.db import get_db

router = APIRouter()


@router.post(
    "/password-reset/request",
    response_model=PasswordResetResponse,
    summary="Request a password-reset email",
)
def request_reset(
    payload: PasswordResetRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    base = getenv("APP_URL", str(request.base_url).rstrip("/"))
    reset_url_template = f"{base}/auth/reset-password/{{token}}"
    message = request_password_reset(db, payload.email, reset_url_template)
    return PasswordResetResponse(message=message)


@router.post(
    "/password-reset/confirm",
    response_model=PasswordResetResponse,
    summary="Reset password using a valid token",
)
def confirm_reset(
    payload: PasswordResetConfirm,
    db: Session = Depends(get_db),
):
    message = confirm_password_reset(db, payload.token, payload.new_password)
    return PasswordResetResponse(message=message)
