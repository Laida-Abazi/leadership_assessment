from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.signup.schemas import VerifyEmailResponse
from app.auth.signup.service import verify_email
from app.db import get_db

router = APIRouter(tags=["auth"])


@router.get("/verify/{token}", response_model=VerifyEmailResponse, summary="Verify email")
def verify_email_endpoint(token: str, db: Session = Depends(get_db)):
    return VerifyEmailResponse(success=verify_email(db, token))
