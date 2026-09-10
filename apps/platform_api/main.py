from pathlib import Path

from fastapi import APIRouter
from fastapi.staticfiles import StaticFiles

from apps.platform_api.routes.agents import router as agents_router
from framework.web.app import create_app
from framework.web.response import ApiResponse, ok

app = create_app(title="Intelligent Service Framework - Platform API", service_name="platform-api")
router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=ApiResponse[dict[str, str]])
async def health():
    return ok({"status": "ok", "service": "platform-api"})


router.include_router(agents_router)
app.include_router(router)

console_dist = Path(__file__).resolve().parents[2] / "frontend" / "console" / "dist"
if console_dist.exists():
    app.mount("/", StaticFiles(directory=console_dist, html=True), name="console")
