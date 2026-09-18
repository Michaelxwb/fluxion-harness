from fastapi import APIRouter, Depends

from .accounts import router as accounts_router
from .agents import router as agents_router
from .auth import public_router as auth_public_router
from .auth import router as auth_router
from .deps import get_current_account, require_admin
from .health import router as health_router
from .internal_channel import router as internal_channel_router
from .internal_runtime import router as internal_runtime_router
from .models import router as models_router
from .platform_adapters import router as platform_adapters_router
from .security import require_csrf
from .skills import router as skills_router
from .users import router as users_router

router = APIRouter()
router.include_router(health_router)
router.include_router(auth_public_router)

authenticated = APIRouter(dependencies=[Depends(get_current_account), Depends(require_csrf)])
authenticated.include_router(auth_router)
authenticated.include_router(agents_router)
authenticated.include_router(models_router)
authenticated.include_router(platform_adapters_router)
authenticated.include_router(skills_router)
router.include_router(authenticated)

admin = APIRouter(dependencies=[Depends(require_admin), Depends(require_csrf)])
admin.include_router(accounts_router)
admin.include_router(users_router)
router.include_router(admin)

router.include_router(internal_runtime_router)
router.include_router(internal_channel_router)
