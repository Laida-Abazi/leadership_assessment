from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.login.schemas import LoginRequest, LoginResponse, TokenPayload, UserBrief
from app.auth.login.service import authenticate_user, create_access_token
from app.db import get_db

router = APIRouter()


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Authenticate and obtain tokens",
)
async def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = authenticate_user(
        db, payload.email, payload.password, request.client.host if request.client else None
    )
    token = create_access_token(user)
    return LoginResponse(
        token=TokenPayload(token=token),
        user=UserBrief(id=user.id, email=user.email, name=user.name, surname=user.surname),
    )
