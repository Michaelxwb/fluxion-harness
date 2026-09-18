import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from muad_agent_runtime.api.deps import get_executor_factory, get_resolve_client
from muad_agent_runtime.application.executor import (
    ExecutorEvent,
    ExecutorFactory,
    ExecutorRequest,
    RunExecutor,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.main import app
from muad_api import AppError
from muad_common import SharedSettings
from muad_contracts import (
    ResolvedAgent,
    ResolveDefinitionRequest,
    ResolveDefinitionResponse,
    ResolvedMcpServer,
    ResolvedModel,
    ResolvedSkill,
)
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

SCHEMA = "runtime"
RUNTIME_TABLES = ("conversation", "run_record", "runtime_snapshot", "canonical_event", "run_interrupt")
CLEANUP_ORDER = ("run_interrupt", "canonical_event", "runtime_snapshot", "run_record", "conversation")
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


def parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        data_lines = [
            line[len("data:") :].lstrip() for line in block.splitlines() if line.startswith("data:")
        ]
        if data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


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
                    for table in RUNTIME_TABLES
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
                tools=[{"name": "list_devices"}],
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


@pytest.fixture
async def client(
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_resolve_client] = lambda: fake_resolve
    app.dependency_overrides[get_executor_factory] = lambda: executor_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.pop(get_resolve_client, None)
    app.dependency_overrides.pop(get_executor_factory, None)
