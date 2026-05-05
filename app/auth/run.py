import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root.parent))

# Load .env from project root so GOOGLE_* and SESSION_SECRET_KEY are available
from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles

from db.database import init_db
from models import Session  # noqa: F401 - register Session with Base for init_db
from oauth.google.config import SESSION_SECRET_KEY


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Auth Service",
    description="Authentication & authorisation template API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Required by Authlib to store OAuth state and temporary data
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)

# Serve local uploads (avatars) in dev.
# In production you typically serve these from S3/CDN instead.
app.mount("/uploads", StaticFiles(directory="uploads", check_dir=False), name="uploads")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


# --- register auth routers here ---
from signup.router import router as signup_router

app.include_router(signup_router, prefix="/auth", tags=["signup"])

from signup.verify_router import router as verify_router

app.include_router(verify_router)

from login.router import router as login_router

app.include_router(login_router, prefix="/auth", tags=["login"])

from password_reset.router import router as password_reset_router

app.include_router(password_reset_router, prefix="/auth", tags=["password-reset"])

from session.main import router as session_router

app.include_router(session_router, prefix="/auth", tags=["session"])

from oauth.google.router import router as oauth_router

app.include_router(oauth_router, prefix="/auth", tags=["oauth"])

from magic_link.router import router as magic_link_router

app.include_router(magic_link_router, prefix="/auth/magic_link", tags=["magic-link"])

from profile.users import router as profile_router

app.include_router(profile_router, tags=["profile"])

# --- teams ---
from teams.teams import router as teams_router

app.include_router(teams_router)

# Ensure Team models are registered on startup so init_db creates tables.
from teams import db_models as _teams_db_models  # noqa: F401


if __name__ == "__main__":
    uvicorn.run("run:app", host="0.0.0.0", port=8000, reload=True)
