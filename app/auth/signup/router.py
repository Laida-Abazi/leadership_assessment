from os import getenv

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.auth.signup.schemas import SignupRequest, SignupResponse, VerifyEmailResponse
from app.auth.signup.service import create_user, verify_email
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
    request: Request,
    db: Session = Depends(get_db),
):
    base = getenv("APP_URL", str(request.base_url).rstrip("/"))
    verification_url_template = f"{base}/auth/verify/{{token}}"
    user = create_user(db, payload, verification_url_template)
    return SignupResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        surname=user.surname,
        message="Account created. Please check your email to verify your account.",
    )


@router.get("/verify/{token}", response_model=VerifyEmailResponse)
def verify_email_endpoint(token: str, db: Session = Depends(get_db)):
    return VerifyEmailResponse(success=verify_email(db, token))
