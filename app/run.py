import logging
import sys
from pathlib import Path

# Ensure project root is on path so "app" is importable (e.g. when running from app/)
_ROOT = Path(__file__).resolve().parent.parent
if _ROOT not in (Path(p).resolve() for p in sys.path):
    sys.path.insert(0, str(_ROOT))

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
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
from app.auth.register import router as auth_router
from app.agent.routes import router as agent_router
from app.routers.intelligence import router as intelligence_router



@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    yield


app = FastAPI(
    title="Leadership Assessment",
    lifespan=lifespan,
)

app.include_router(job_requirements_router)
app.include_router(assessments_router)
app.include_router(analysis_router)
app.include_router(auth_router)
app.include_router(agent_router)
app.include_router(intelligence_router)



@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/test/pipeline")
def serve_test_pipeline():
    """Serve the end-to-end pipeline test UI."""
    path = Path(__file__).resolve().parent / "templates" / "test_pipeline.html"
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
