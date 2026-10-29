import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from muad_agent_runtime.api.deps import (
    get_credentials_client,
    get_executor_factory,
    get_platform_settings_client,
    get_resolve_client,
)
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.executor import (
    ExecutorEvent,
    ExecutorFactory,
    ExecutorRequest,
    RunExecutor,
)
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.main import app
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts import (
    ResolvedAgent,
    ResolveDefinitionRequest,
    ResolveDefinitionResponse,
    ResolvedMcpServer,
    ResolvedModel,
    ResolvedSkill,
    ResolveModelRequest,
    ResolveModelResponse,
)
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

SCHEMA = "runtime"
from tests.async_tool_datastores import async_tool_database, async_tool_datastore  # noqa: F401, E402
from tests.internal_service import internal_service_token  # noqa: F401, E402  (fixture 注册)

RUNTIME_TABLES = ("conversation", "run_record", "runtime_snapshot", "canonical_event", "run_interrupt")
#: 清理顺序：通知行先于 feed 与 run_record/conversation（同 Owner 物理 FK）；终端 Run 会留通知行。
CLEANUP_ORDER = (
    "channel_notification",
    "channel_notification_feed",
    "memory_consolidation_job",
    "memory_command_receipt",
    "user_memory_history",
    "user_memory",
    "memory_scope",
    "tool_control_outbox",
    "tool_result_inbox",
    "run_continuation",
    "tool_operation",
    "run_interrupt",
    "canonical_event",
    "run_submission",
    "runtime_snapshot",
    "run_record",
    "conversation",
)
FAKE_DELTAS = ("runtime ", "execution ", "engine")


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    agent_id: uuid.UUID
    platform_user_id: uuid.UUID


@dataclass
class FakeResolveClient:
    response: ResolveDefinitionResponse
    error: AppError | None = None
    calls: list[ResolveDefinitionRequest] = field(default_factory=list)
    #: `resolve_model`（ADR-06 摘要模型）按主键给出的定义；空 ⇒ 一律 COMMON_NOT_FOUND
    summary_models: dict[uuid.UUID, ResolvedModel] = field(default_factory=dict)
    model_calls: list[ResolveModelRequest] = field(default_factory=list)

    async def resolve(
        self,
        request: ResolveDefinitionRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
    ) -> ResolveDefinitionResponse:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.response

    async def resolve_model(
        self,
        request: ResolveModelRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
    ) -> ResolveModelResponse:
        self.model_calls.append(request)
        model = self.summary_models.get(request.model_id)
        if model is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return ResolveModelResponse(model=model)


def parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        data_lines = [
            line[len("data:") :].lstrip() for line in block.splitlines() if line.startswith("data:")
        ]
        if data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


@pytest.fixture(scope="session", autouse=True)
def fresh_engine_caches() -> None:
    """进程内其它测试（如验收真实服务线程）可能已创建绑定旧 loop 的 engine：本套件统一重建。"""
    from muad_agent_runtime.infrastructure.db import get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()


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
                    inspect(sync_connection).has_table(table, schema=SCHEMA) for table in RUNTIME_TABLES
                )
            )
        if not ready:
            pytest.skip("run: uv run alembic -c migrations/alembic.ini upgrade head")
        yield
    finally:
        await engine.dispose()


@pytest.fixture
async def tenant(database_guard: None) -> AsyncIterator[TenantContext]:
    context = TenantContext(
        tenant_id=f"test-{uuid.uuid4()}",
        agent_id=uuid.uuid4(),
        platform_user_id=uuid.uuid4(),
    )
    try:
        yield context
    finally:
        session_factory = get_session_factory()
        async with session_factory() as session:
            for table in CLEANUP_ORDER:
                await session.execute(
                    text(f"DELETE FROM runtime.{table} WHERE tenant_id = :tenant_id"),
                    {"tenant_id": context.tenant_id},
                )
            await session.commit()


@pytest.fixture
def resolved(tenant: TenantContext) -> ResolveDefinitionResponse:
    return ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=tenant.agent_id,
            key="skeleton-agent",
            revision=3,
            instructions="You are a deterministic skeleton agent.",
            runtime_config={"temperature": 0.0},
        ),
        model=ResolvedModel(
            id=uuid.uuid4(),
            revision=2,
            model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1",
            api_key="sk-demo",
            params={"temperature": 0.0},
        ),
        skills=[
            ResolvedSkill(
                skill_id=uuid.uuid4(),
                artifact_id=uuid.uuid4(),
                key="policy-check",
                name="Policy Check",
                description="demo skill",
                version="1.0.0",
                checksum="sha256:" + "a" * 64,
                storage_key="skills/demo/policy-check.zip",
                execution_mode="SYNC",
            )
        ],
        mcp_servers=[
            ResolvedMcpServer(
                mcp_server_id=uuid.uuid4(),
                key="inventory",
                endpoint="https://mcp.example.com/mcp",
                catalog_revision=1,
                definitions=[{"name": "list_devices"}],
            )
        ],
    )


@pytest.fixture
def fake_resolve(resolved: ResolveDefinitionResponse) -> FakeResolveClient:
    return FakeResolveClient(response=resolved)


class FakeExecutor:
    def __init__(self, request: ExecutorRequest) -> None:
        self._request = request

    async def run(self) -> AsyncIterator[ExecutorEvent]:
        for delta in FAKE_DELTAS:
            if await self._request.is_cancel_requested():
                return
            yield ExecutorEvent(type="message.delta", data={"delta": delta})


async def fake_executor_factory(request: ExecutorRequest) -> RunExecutor:
    return FakeExecutor(request)


@pytest.fixture
def executor_factory() -> ExecutorFactory:
    return fake_executor_factory


@pytest.fixture(autouse=True)
async def execution_supervisor_boundary():
    supervisor = ExecutionSupervisor()
    app.state.execution_supervisor = supervisor
    app.state.instance_id = "test-instance"
    try:
        yield supervisor
    finally:
        await supervisor.close()


class _SuitePlatformSettings:
    """本套件的默认设置源：revision 0（该租户无记录）+ **显式** capacity 组。

    准入按 schema 默认（每用户 2 个非终态 Run）会挡住既有「同一用户连建 3 个 Run」的用例
    ——终态释放属 TASK-031；本套件因此显式声明足够大的容量组，其余分组仍回落 schema 默认
    （冻结策略与「无设置」逐键一致）。真实取值路径与失败路径见 `test_platform_settings_source.py`。
    """

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=0,
            settings={
                "capacity": {
                    "max_open_runs_per_tenant": 100_000,
                    "max_open_runs_per_user": 100,
                },
                # 既有套件大量使用固定日期种子；会话保留/上限由专门用例显式覆盖，
                # 这里保持「不按时间失效 + 足够大的上限」，避免产品默认值改变既有断言。
                "webchat": {
                    "conversation_retention_days": 0,
                    "max_conversations_per_entry": 1000,
                },
            },
        )


@pytest.fixture(autouse=True)
def no_platform_settings_client() -> AsyncIterator[None]:
    """本套件默认「无平台设置」：Run 创建不真打内部端点（真实取值路径另有专门用例覆盖）。"""
    app.dependency_overrides[get_platform_settings_client] = lambda: _SuitePlatformSettings()
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_platform_settings_client, None)


@pytest.fixture
async def client(
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_resolve_client] = lambda: fake_resolve
    app.dependency_overrides[get_executor_factory] = lambda: executor_factory
    app.dependency_overrides[get_credentials_client] = lambda: None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.pop(get_resolve_client, None)
    app.dependency_overrides.pop(get_executor_factory, None)
    app.dependency_overrides.pop(get_credentials_client, None)
