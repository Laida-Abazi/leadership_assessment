from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.auth.login.deps import ACCESS_TOKEN_COOKIE_NAME, require_authenticated_user
from app.auth.login.schemas import LoginRequest, LoginResponse, TokenPayload, UserBrief
from app.auth.login.service import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    authenticate_user,
    create_access_token,
)
from app.db import get_db
from app.db.models import User

router = APIRouter()


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Authenticate and obtain tokens",
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    user = authenticate_user(
        db, payload.email, payload.password, request.client.host if request.client else None
    )
    token = create_access_token(user)
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        secure=(request.url.scheme == "https"),
        samesite="lax",
        path="/",
    )
    return LoginResponse(
        token=TokenPayload(token=token),
        user=UserBrief(id=user.id, email=user.email, name=user.name, surname=user.surname),
    )


@router.get(
    "/me",
    response_model=UserBrief,
    summary="Return the currently authenticated user",
)
async def get_authenticated_user_profile(
    user: User = Depends(require_authenticated_user),
):
    return UserBrief(id=user.id, email=user.email, name=user.name, surname=user.surname)
