from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.auth.candidate_access import CANDIDATE_ACCESS_COOKIE_NAME
from app.auth.login.deps import ACCESS_TOKEN_COOKIE_NAME, require_authenticated_user
from app.auth.logout.schemas import LogoutResponse
from app.auth.logout.service import logout_user
from app.db import get_db
from app.db.models import User

router = APIRouter()


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="Log out and invalidate refresh token",
)
async def logout(
    response: Response,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    logout_user(db, user.id)
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE_NAME, path="/")
    response.delete_cookie(key=CANDIDATE_ACCESS_COOKIE_NAME, path="/")
    return LogoutResponse()
