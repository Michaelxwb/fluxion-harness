from fastapi import APIRouter

from .agents import router as agents_router
from .health import router as health_router

router = APIRouter()
router.include_router(health_router)
router.include_router(agents_router)
