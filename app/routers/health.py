from fastapi import APIRouter

from app.celery.celery_app import celery_app
from app.services.redis_client import ping_redis

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/")
async def health_check():
    redis_ok = ping_redis()

    # Keep this probe short so the endpoint stays responsive when workers are down.
    try:
        inspector = celery_app.control.inspect(timeout=1.0)
        active = inspector.active()
        celery_ok = active is not None
    except Exception:
        celery_ok = False

    return {
        "status": "ok" if (redis_ok and celery_ok) else "degraded",
        "redis": "ok" if redis_ok else "unreachable",
        "celery_workers": "ok" if celery_ok else "no workers found",
    }
