import logging
import sys
from pathlib import Path

# Ensure project root is on path so "app" is importable (e.g. when running from app/)
_ROOT = Path(__file__).resolve().parent.parent
if _ROOT not in (Path(p).resolve() for p in sys.path):
    sys.path.insert(0, str(_ROOT))

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

import uvicorn

# So agent and other app loggers show up in the same console as uvicorn
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(name)s - %(message)s",
    stream=sys.stderr,
    force=True,
)

from app.as_requirements.routes.ai_analysis import router as job_requirements_router
from app.as_blueprinting.routes.assessment import router as assessments_router
from app.as_analysis.routes.analysis import router as analysis_router
from app.auth.router import router as auth_router
from app.auth.login.deps import require_authenticated_user
from app.agent.routes import router as agent_router
from app.db import SessionLocal
from app.routers.intelligence import router as intelligence_router
from app.services.assessment_registry import ensure_assessment_types_seeded



@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    db = SessionLocal()
    try:
        try:
            ensure_assessment_types_seeded(db)
        except Exception:
            logging.getLogger(__name__).warning(
                "Assessment type registry could not be seeded during startup. "
                "Run the latest migrations before using multi-assessment features.",
                exc_info=True,
            )
    finally:
        db.close()
    yield


app = FastAPI(
    title="Multi Assessment Agent",
    lifespan=lifespan,
)

CORS_ORIGINS = [
    "https://leadership-assessment-front-app.vercel.app",
    "http://localhost:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
protected_dependencies = [Depends(require_authenticated_user)]
app.include_router(job_requirements_router, dependencies=protected_dependencies)
app.include_router(assessments_router, dependencies=protected_dependencies)
app.include_router(analysis_router, dependencies=protected_dependencies)
app.include_router(agent_router, dependencies=protected_dependencies)
app.include_router(intelligence_router, dependencies=protected_dependencies)



@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/test/pipeline")
def serve_test_pipeline():
    """Serve the end-to-end pipeline test UI."""
    path = Path(__file__).resolve().parent / "templates" / "test_pipeline.html"
    content = path.read_text(encoding="utf-8")
    return HTMLResponse(content, headers={"Cache-Control": "no-store"})


@app.get("/auth/login")
def serve_login_page():
    """Serve the login UI."""
    path = Path(__file__).resolve().parent / "templates" / "auth_login.html"
    content = path.read_text(encoding="utf-8")
    return HTMLResponse(content, headers={"Cache-Control": "no-store"})


@app.get("/auth/signup")
def serve_signup_page():
    """Serve the signup UI."""
    path = Path(__file__).resolve().parent / "templates" / "auth_signup.html"
    content = path.read_text(encoding="utf-8")
    return HTMLResponse(content, headers={"Cache-Control": "no-store"})


def get_app():
    return app


if __name__ == "__main__":
    uvicorn.run(
        "app.run:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
