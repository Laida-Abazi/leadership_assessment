import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User


async def logout_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Invalidate the current session by clearing the user's refresh token."""
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(refresh_token=None, refresh_token_expires=None)
    )
    await db.flush()
