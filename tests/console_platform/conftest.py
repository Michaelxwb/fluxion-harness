import hashlib
import shutil
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from muad_agent_runtime.application.ports import NullPlatformSettingsClient
from muad_api import install_api_foundation
from muad_common import SharedSettings
from muad_common.session_store import RedisSessionStore
from muad_console_platform.api.deps import get_runtime_memory_client
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from muad_console_platform.infrastructure.models.control import (
    ModelCredential,
    ModelDefinition,
    ModelDeployment,
)
from muad_console_platform.infrastructure.runtime_memory_client import RuntimeMemoryClient
from muad_console_platform.main import app
from redis.asyncio import Redis
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.internal_service import TOKEN as INTERNAL_SERVICE_TOKEN
from tests.internal_service import internal_service_token  # noqa: F401  (夹具注册，按需依赖)


@pytest.fixture(scope="session", autouse=True)
async def chat_session_store() -> AsyncIterator[None]:
    """ASGITransport 不跑 lifespan：补真实 Redis 会话权威，与生产装配同语义。"""
    settings = SharedSettings()
    if not settings.redis_url:
        yield
        return
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.session_store = RedisSessionStore(client)
    try:
        yield
    finally:
        app.state.session_store = None
        await client.aclose()


SCHEMA = "control"
AGENT_TABLE = "agent_definition"
MODEL_TABLE = "model_definition"
AUTH_TABLES = ("console_account", "console_session")
ADMIN_PASSWORD = "console-admin-password"
#: 夹具模型的执行入口：resolve-* 返回的即这行成员凭据（逻辑模型只声明供方）
FIXTURE_PROVIDER_KEY = "openai"
FIXTURE_MODEL_BASE_URL = "https://api.example.com/v1"
FIXTURE_MODEL_API_KEY = "sk-console-fixture"


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    model_id: uuid.UUID
    disabled_model_id: uuid.UUID


@pytest.fixture(scope="session")
async def database_guard() -> AsyncIterator[None]:
    settings = SharedSettings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            ready = await connection.run_sync(
                lambda sync_connection: all(
                    inspect(sync_connection).has_table(table, schema=SCHEMA)
                    for table in (AGENT_TABLE, MODEL_TABLE, *AUTH_TABLES)
                )
            )
        if not ready:
            pytest.skip("run: uv run alembic -c migrations/alembic.ini upgrade head")
        yield
    finally:
        await engine.dispose()


@pytest.fixture
async def tenant(database_guard: None) -> AsyncIterator[TenantContext]:
    tenant_id = f"test-{uuid.uuid4()}"
    session_factory = get_session_factory()
    async with session_factory() as session:
        enabled_model = ModelDefinition(
            tenant_id=tenant_id,
            key=f"model-{uuid.uuid4()}",
            name="Enabled Model",
            model_id="gpt-4o-mini",
            provider_key=FIXTURE_PROVIDER_KEY,
            params_json={"temperature": 0.0, "max_tokens": 1024},
            enabled=True,
        )
        disabled_model = ModelDefinition(
            tenant_id=tenant_id,
            key=f"model-{uuid.uuid4()}",
            name="Disabled Model",
            model_id="gpt-4o-mini",
            params_json={"temperature": 0.0, "max_tokens": 1024},
            enabled=False,
        )
        session.add_all([enabled_model, disabled_model])
        await session.commit()
        context = TenantContext(
            tenant_id=tenant_id,
            model_id=enabled_model.id,
            disabled_model_id=disabled_model.id,
        )
    try:
        yield context
    finally:
        async with session_factory() as session:
            skill_ids = list(
                await session.scalars(
                    text("SELECT id FROM control.skill WHERE tenant_id = :tenant_id").bindparams(
                        tenant_id=tenant_id
                    )
                )
            )
            for statement in (
                "DELETE FROM control.agent_skill_binding WHERE agent_id IN "
                "(SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.agent_mcp_binding WHERE agent_id IN "
                "(SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.agent_access_grant WHERE agent_id IN "
                "(SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.bot_account WHERE agent_id IN "
                "(SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id",
                "DELETE FROM control.skill_user_grant WHERE skill_id IN "
                "(SELECT id FROM control.skill WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.skill_artifact WHERE skill_id IN "
                "(SELECT id FROM control.skill WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.skill WHERE tenant_id = :tenant_id",
                "DELETE FROM control.skill_import_idempotency WHERE tenant_id = :tenant_id",
                "DELETE FROM control.mcp_user_grant WHERE mcp_server_id IN "
                "(SELECT id FROM control.mcp_server WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.mcp_server WHERE tenant_id = :tenant_id",
                "DELETE FROM control.bind_code WHERE platform_user_id IN "
                "(SELECT id FROM control.platform_user WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.channel_identity WHERE platform_user_id IN "
                "(SELECT id FROM control.platform_user WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
                "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
                # 成员 → 凭据 → 逻辑模型：外键要求先解绑再删凭据，最后删模型
                "DELETE FROM control.model_deployment WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_definition WHERE tenant_id = :tenant_id",
            ):
                await session.execute(text(statement), {"tenant_id": tenant_id})
            await session.commit()
        artifact_root = Path(SharedSettings().artifact_root)
        for skill_id in skill_ids:
            shutil.rmtree(artifact_root / "skills" / str(skill_id), ignore_errors=True)


@pytest.fixture
async def model_member(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    """给夹具的启用模型补一条成员凭据（执行入口）。

    只有真正走 resolve/执行入口的用例依赖它：夹具默认不建凭据，池资源类用例的
    租户作用域计数（凭据/成员/额度组）不被夹具噪声污染。清理由 `tenant` 收尾统一完成。
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        credential = ModelCredential(
            tenant_id=tenant.tenant_id,
            key=f"cred-{uuid.uuid4()}",
            provider_key=FIXTURE_PROVIDER_KEY,
            base_url=FIXTURE_MODEL_BASE_URL,
            api_key=FIXTURE_MODEL_API_KEY,
            api_key_digest=hashlib.sha256(FIXTURE_MODEL_API_KEY.encode()).hexdigest(),
            max_inflight=8,
        )
        session.add(credential)
        await session.flush()
        session.add(
            ModelDeployment(
                tenant_id=tenant.tenant_id,
                key=f"member-{uuid.uuid4()}",
                model_id=tenant.model_id,
                credential_id=credential.id,
                weight=1,
            )
        )
        await session.commit()
    yield tenant


@pytest.fixture
async def client(tenant: TenantContext) -> AsyncIterator[AsyncClient]:
    username = f"admin-{uuid.uuid4()}"
    session_factory = get_session_factory()
    async with session_factory() as session:
        account = ConsoleAccount(
            tenant_id=tenant.tenant_id,
            username=username,
            display_name="Console Admin",
            password_hash=hash_password(ADMIN_PASSWORD),
            role=ROLE_ADMIN,
        )
        session.add(account)
        await session.commit()
        account_id = account.id
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        login_response = await http_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": ADMIN_PASSWORD},
            headers={"X-Tenant-Id": tenant.tenant_id},
        )
        assert login_response.status_code == 200
        csrf_token = http_client.cookies.get(CSRF_COOKIE)
        assert csrf_token
        http_client.headers[CSRF_HEADER] = csrf_token
        yield http_client
    async with session_factory() as session:
        await session.execute(
            text("DELETE FROM control.console_session WHERE account_id = :account_id"),
            {"account_id": account_id},
        )
        await session.execute(
            text("DELETE FROM control.console_account WHERE id = :account_id"),
            {"account_id": account_id},
        )
        await session.commit()


async def delete_login_audit_rows(session: AsyncSession, tenant_id: str) -> None:
    """清掉 `client` fixture 真实登录写入的认证审计行（`resource_type='CONSOLE_ACCOUNT'`）。

    审计聚合类用例以「本租户审计行 == 种子行」为前置，而登录审计是 fixture 的认证副作用、
    不属于其被测对象：不清掉它，租户级 total/集合断言会多出一行。认证审计本身由
    console-auth 场景自行验证，这里只恢复确定性基线。
    """
    await session.execute(
        text(
            "DELETE FROM control.config_audit_log "
            "WHERE tenant_id = :tenant_id AND resource_type = 'CONSOLE_ACCOUNT'"
        ),
        {"tenant_id": tenant_id},
    )


def runtime_memory_app() -> FastAPI:
    """真实 Runtime 记忆管理路由：真实服务身份门控 + 真实唯一事务写服务 + 真实 session。"""
    module = import_module("muad_agent_runtime.api.memory")
    runtime_app = FastAPI()
    install_api_foundation(runtime_app)
    # 唯一替换：Runtime 的 platform-settings 端口用仓库自带的 Null 实现（默认记忆策略），
    # 否则会真的去连 dev Console。写服务、门控与数据库都是真的。
    runtime_app.state.platform_settings_client = NullPlatformSettingsClient()
    runtime_app.include_router(module.router)
    return runtime_app


@pytest.fixture
async def runtime_memory_client() -> AsyncIterator[RuntimeMemoryClient]:
    """Console → Runtime 的真实 HTTP 通道（ASGI 直连真实 Runtime 路由）。"""
    client = RuntimeMemoryClient(
        "http://runtime",
        service_token=INTERNAL_SERVICE_TOKEN,
        transport=httpx.ASGITransport(app=runtime_memory_app()),
    )
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def runtime_memory_override(
    runtime_memory_client: RuntimeMemoryClient,
    internal_service_token: None,  # noqa: F811  (夹具依赖：Console 与 Runtime 共用同一服务身份)
) -> Iterator[None]:
    """把 Console 的 Runtime 客户端依赖指到上面那条真实通道。"""
    app.state.runtime_memory_client = None
    app.dependency_overrides[get_runtime_memory_client] = lambda: runtime_memory_client
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_runtime_memory_client, None)
        app.state.runtime_memory_client = None
