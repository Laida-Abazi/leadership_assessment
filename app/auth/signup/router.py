from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth.signup.schemas import SignupRequest, SignupResponse
from app.auth.signup.service import EMAIL_VERIFICATION_ENABLED, create_user
from app.auth.urls import build_frontend_verification_url_template
from app.db import get_db

router = APIRouter()


@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
def signup(
    payload: SignupRequest,
    db: Session = Depends(get_db),
):
    verification_url_template = build_frontend_verification_url_template()
    user = create_user(db, payload, verification_url_template)
    message = (
        "Account created. Please check your email to verify your account."
        if EMAIL_VERIFICATION_ENABLED
        else "Account created. You can log in now."
    )
    return SignupResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        surname=user.surname,
        message=message,
    )
