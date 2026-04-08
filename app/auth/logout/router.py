import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from login.deps import get_current_user_id
from logout.schemas import LogoutResponse
from logout.service import logout_user

router = APIRouter()


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="Log out and invalidate refresh token",
)
async def logout(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await logout_user(db, user_id)
    await db.commit()
    return LogoutResponse()
