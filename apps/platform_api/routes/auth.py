from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from adapters.postgres.auth_repository import AuthRepository
from apps.platform_api.dependencies import current_principal, get_session_factory
from framework.web.response import ApiResponse, ok

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    token: str
    username: str
    role: str


@router.post("/login", response_model=ApiResponse[LoginResponse])
async def login(body: LoginRequest) -> ApiResponse[LoginResponse]:
    repository = AuthRepository(get_session_factory())
    token, principal = await repository.login(body.username, body.password)
    return ok(LoginResponse(token=token, username=principal.username, role=principal.role))


@router.post("/logout", response_model=ApiResponse[dict[str, str]])
async def logout(authorization: str | None = Header(default=None)) -> ApiResponse[dict[str, str]]:
    await current_principal(authorization)  # 401 when the token is invalid
    repository = AuthRepository(get_session_factory())
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    await repository.logout(token)
    return ok({"status": "logged_out"})
