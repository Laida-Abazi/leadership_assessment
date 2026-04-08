from fastapi import APIRouter

from app.auth.login.router import router as login_router
from app.auth.password_reset.router import router as password_reset_router
from app.auth.signup.router import router as signup_router

router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(signup_router)
router.include_router(login_router)
router.include_router(password_reset_router)
