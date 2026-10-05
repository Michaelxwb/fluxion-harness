"""TASK-005 · Runtime 平台设置读取缝与冻结（E-03 / E-07 / E-08）。

真实边界（业务路径不 mock）：
- 真实 PostgreSQL：`control.platform_setting` 版本行、`control.agent_definition` 覆盖行、
  `runtime.runtime_snapshot.policy_json` 冻结行；
- 真实 Console 内部 HTTP 端点：`GET /internal/v1/platform-settings` 由真实 uvicorn 单进程监听
  127.0.0.1 真实 socket，Runtime 的 `ConsolePlatformSettingsClient` 经真实 TCP 取快照；
- 独立客户端：每个新 Run 新建 service + client，证明「无进程内 TTL 缓存、无重启、无等待窗口」；
- E-03 的 Console 端点是真实被切断的（连到一个已释放端口），失败计数在**调用方**侧记录。

三条合同场景的命令逐字命中 `-k`：`source_unavailable` / `agent_override_precedence` /
`new_revision_without_restart`。
"""

from __future__ import annotations

import asyncio
import socket
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI
from muad_agent_runtime.api.deps import (
    get_credentials_client,
    get_executor_factory,
    get_platform_settings_client,
    get_resolve_client,
)
from muad_agent_runtime.application.executor import ExecutorFactory
from muad_agent_runtime.application.run_service import RunService
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import RunRecord, RuntimeSnapshot
from muad_agent_runtime.infrastructure.platform_settings_client import (
    PLATFORM_SETTINGS_PATH,
    ConsolePlatformSettingsClient,
)
from muad_agent_runtime.main import app as runtime_app
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory as console_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from muad_console_platform.infrastructure.models.control import (
    AgentDefinition,
    ModelDefinition,
    PlatformSetting,
)
from muad_console_platform.main import app as console_app
from muad_contracts import (
    ChannelContext,
    MessageInput,
    ResolvedAgent,
    ResolveDefinitionResponse,
    ResolvedModel,
    RunRequest,
)
from muad_contracts.platform_settings import default_compaction_settings

from agent_runtime.conftest import FakeResolveClient, TenantContext

LISTEN_TIMEOUT_SEC = 15.0
#: 内部服务身份：与 Console `require_service_identity` 同源（env `INTERNAL_SERVICE_TOKEN`）。
INTERNAL_TOKEN = "test-internal-token"
DEFAULT_MAX_GROUPS = default_compaction_settings().snip.max_groups  # schema 默认（无平台设置时）


@pytest.fixture(autouse=True)
def _internal_service_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", INTERNAL_TOKEN)


# ---- 真实服务（uvicorn 单进程 + 真实 socket）----


@asynccontextmanager
async def _serve_app(target: FastAPI) -> AsyncIterator[str]:
    """真实 uvicorn 单进程监听 127.0.0.1 随机端口，产出真实 base_url。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    config = uvicorn.Config(target, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        deadline = time.monotonic() + LISTEN_TIMEOUT_SEC
        while not server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert server.started, "目标服务未在超时内监听"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=LISTEN_TIMEOUT_SEC)


@asynccontextmanager
async def _dead_endpoint() -> AsyncIterator[str]:
    """一个刚被释放的 127.0.0.1 端口：真实连接被拒（Console 内部端点被切断）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    yield f"http://127.0.0.1:{port}"


@asynccontextmanager
async def _console_server() -> AsyncIterator[str]:
    """真实 Console app（含内部取设置端点 + admin 读接口），真实 socket。"""
    from muad_console_platform.infrastructure import db as console_db

    console_db.get_engine.cache_clear()
    console_db.get_session_factory.cache_clear()
    async with _serve_app(console_app) as url:
        yield url


def _settings_client(console_url: str) -> ConsolePlatformSettingsClient:
    return ConsolePlatformSettingsClient(console_url, service_token=INTERNAL_TOKEN)


# ---- 种子数据 / 清理 ----


async def _seed_platform_setting(tenant_id: str, revision: int, document: dict[str, Any]) -> None:
    async with console_session_factory()() as session:
        session.add(PlatformSetting(tenant_id=tenant_id, revision=revision, settings_json=document))
        await session.commit()


async def _seed_agent_with_compaction_override(
    tenant_id: str, *, max_groups: int
) -> dict[str, Any]:
    """真实 `control.agent_definition` 行，其 `runtime_config_json.budget.compaction` 非空。"""
    async with console_session_factory()() as session:
        model = ModelDefinition(
            tenant_id=tenant_id,
            key=f"model-{uuid.uuid4()}",
            name="Seeded Model",
            model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1",
            revision=1,
            enabled=True,
        )
        session.add(model)
        await session.flush()
        runtime_config = {"budget": {"compaction": {"snip": {"max_groups": max_groups}}}}
        agent = AgentDefinition(
            tenant_id=tenant_id,
            key=f"agent-{uuid.uuid4()}",
            name="Seeded Agent",
            instructions="seeded",
            model_id=model.id,
            runtime_config=runtime_config,
            revision=1,
            enabled=True,
        )
        session.add(agent)
        await session.commit()
        return runtime_config


@pytest.fixture
async def control_cleanup(tenant: TenantContext) -> AsyncIterator[None]:
    try:
        yield
    finally:
        async with console_session_factory()() as session:
            await session.execute(
                sa.text("DELETE FROM control.platform_setting WHERE tenant_id = :t"),
                {"t": tenant.tenant_id},
            )
            await session.execute(
                sa.text(
                    "DELETE FROM control.agent_definition WHERE tenant_id = :t"
                ),
                {"t": tenant.tenant_id},
            )
            await session.execute(
                sa.text("DELETE FROM control.model_definition WHERE tenant_id = :t"),
                {"t": tenant.tenant_id},
            )
            await session.execute(
                sa.text(
                    "DELETE FROM control.console_session WHERE account_id IN "
                    "(SELECT id FROM control.console_account WHERE tenant_id = :t)"
                ),
                {"t": tenant.tenant_id},
            )
            await session.execute(
                sa.text("DELETE FROM control.console_account WHERE tenant_id = :t"),
                {"t": tenant.tenant_id},
            )
            await session.commit()


# ---- Run 组装 / 读冻结值 ----


def _request(tenant: TenantContext, text: str = "hello settings") -> RunRequest:
    return RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        channel=ChannelContext(type="WECOM", bot_id="bot-1"),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text=text),
    )


async def _drain(events: AsyncIterator[Any]) -> None:
    async for _ in events:
        pass


async def _start_run(
    tenant: TenantContext,
    console_url: str,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
) -> uuid.UUID:
    """用**新构造**的 service + client 创建一个 Run，并驱动其到终态。"""
    client = _settings_client(console_url)
    try:
        async with get_session_factory()() as session:
            service = RunService(
                session,
                fake_resolve,
                "instance-a",
                executor_factory=executor_factory,
                settings_client=client,
            )
            started = await service.start(_request(tenant), tenant.tenant_id)
            await _drain(started.events)
            return started.run_id
    finally:
        await client.aclose()


async def _frozen_policy(run_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None and run.snapshot_id is not None
        snapshot = await session.get(RuntimeSnapshot, run.snapshot_id)
        assert snapshot is not None
        return snapshot.policy_json


def _resolved_with_override(
    tenant: TenantContext, runtime_config: dict[str, Any]
) -> ResolveDefinitionResponse:
    return ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=tenant.agent_id,
            key="seeded-agent",
            revision=1,
            instructions="seeded",
            runtime_config=runtime_config,
        ),
        model=ResolvedModel(
            id=uuid.uuid4(),
            revision=1,
            model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1",
            api_key="sk-demo",
        ),
        skills=[],
        mcp_servers=[],
    )


async def _login_admin(http: httpx.AsyncClient, tenant_id: str) -> None:
    username = f"admin-{uuid.uuid4()}"
    async with console_session_factory()() as session:
        session.add(
            ConsoleAccount(
                tenant_id=tenant_id,
                username=username,
                display_name="Admin",
                password_hash=hash_password("console-admin-password"),
                role=ROLE_ADMIN,
            )
        )
        await session.commit()
    response = await http.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "console-admin-password"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert response.status_code == 200, response.text


# ---- E-03：设置源不可读 ⇒ Run 创建明确失败 + 调用方失败计数 ----


def _failed_fetch_total(text: str) -> float:
    #: 只看 Runtime 这一条 label：同一进程里 Worker/Gateway 侧也会记 failed（TASK-006），
    #: 不带 `caller` 过滤会读到别的调用方的计数。
    for line in text.splitlines():
        if (
            line.startswith("platform_settings_fetch_total")
            and 'caller="runtime"' in line
            and 'result="failed"' in line
        ):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


async def test_e03_source_unavailable_fails_run_creation_with_failed_metric(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    control_cleanup: None,
) -> None:
    """[E-03][RULE-06] 真实 Runtime 进程 + 被切断的 Console 内部端点。

    Run 创建必须**明确失败**（统一错误码 + 调用方失败计数），绝不静默回退到过期默认值；
    失败发生在任何落库之前，因此不会留下 `runtime_snapshot` 行。
    """
    async with _dead_endpoint() as dead_url:
        cut_client = ConsolePlatformSettingsClient(
            dead_url, service_token=INTERNAL_TOKEN, timeout_sec=0.5
        )
        runtime_app.dependency_overrides[get_resolve_client] = lambda: fake_resolve
        runtime_app.dependency_overrides[get_executor_factory] = lambda: executor_factory
        runtime_app.dependency_overrides[get_credentials_client] = lambda: None
        runtime_app.dependency_overrides[get_platform_settings_client] = lambda: cut_client
        try:
            async with _serve_app(runtime_app) as base_url:
                async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as http:
                    before = _failed_fetch_total((await http.get("/metrics")).text)
                    response = await http.post(
                        "/v1/runs",
                        json={
                            "agent_id": str(tenant.agent_id),
                            "platform_user_id": str(tenant.platform_user_id),
                            "channel": {"type": "WECOM", "bot_id": "bot-1"},
                            "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": "x"},
                        },
                        headers={"X-Tenant-Id": tenant.tenant_id},
                    )
                    assert response.status_code >= 500, response.text
                    assert response.json()["code"] == "COMMON_INTERNAL_ERROR", response.text
                    after = _failed_fetch_total((await http.get("/metrics")).text)
        finally:
            runtime_app.dependency_overrides.pop(get_resolve_client, None)
            runtime_app.dependency_overrides.pop(get_executor_factory, None)
            runtime_app.dependency_overrides.pop(get_credentials_client, None)
            runtime_app.dependency_overrides.pop(get_platform_settings_client, None)
            await cut_client.aclose()

    assert after == before + 1, "取快照失败必须由调用方记 platform_settings_fetch_total{failed}"

    # 明确失败、无过期默认值参与装配：没有留下任何冻结 snapshot 行
    async with get_session_factory()() as session:
        frozen = await session.scalar(
            sa.select(sa.func.count())
            .select_from(RuntimeSnapshot)
            .where(RuntimeSnapshot.tenant_id == tenant.tenant_id)
        )
    assert frozen == 0, "源不可读时不得用任何默认值冻结出 snapshot"


# ---- E-07：Agent 显式覆盖优先于平台默认 ----


async def test_e07_agent_override_precedence_over_platform_default(
    tenant: TenantContext,
    executor_factory: ExecutorFactory,
    control_cleanup: None,
) -> None:
    """[E-07][RULE-02] 真实 PG + 真实 Agent 定义行 + 真实内部端点。

    Agent 的 `runtime_config.budget.compaction` 覆盖平台默认（该键取 Agent 值）；
    平台设置里 Agent 没覆盖的键仍生效；admin 读接口如实返回压缩分组的资源覆盖数。
    """
    override_groups = 60
    platform_groups = 40
    runtime_config = await _seed_agent_with_compaction_override(
        tenant.tenant_id, max_groups=override_groups
    )
    fake = FakeResolveClient(response=_resolved_with_override(tenant, runtime_config))

    async with _console_server() as console_url:
        await _seed_platform_setting(
            tenant.tenant_id,
            revision=1,
            document={"compaction": {"snip": {"max_groups": platform_groups}, "micro": {"enabled": True}}},
        )
        run_id = await _start_run(tenant, console_url, fake, executor_factory)
        policy = await _frozen_policy(run_id)

        async with httpx.AsyncClient(base_url=console_url, timeout=30.0) as http:
            await _login_admin(http, tenant.tenant_id)
            read = await http.get("/api/v1/platform-settings")
            assert read.status_code == 200, read.text
            groups = read.json()["data"]["groups"]

    # 覆盖值优先：Agent 显式 60 胜过平台 40
    assert policy["compaction"]["snip"]["max_groups"] == override_groups
    # 平台生效：Agent 未覆盖的 micro 用平台值
    assert policy["compaction"]["micro"]["enabled"] is True
    # 未覆盖的 leaf 回落 schema 默认
    assert policy["compaction"]["snip"]["keep_head_groups"] == 3

    compaction_group = next(group for group in groups if group["key"] == "compaction")
    overridden = {field["overridden_by_resources"] for field in compaction_group["fields"]}
    assert overridden == {1}, "读接口必须如实返回压缩分组的资源覆盖数量"


# ---- E-08：保存后新 Run 立即读到新 revision（无 TTL、无重启）----


async def test_e08_new_revision_without_restart(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    control_cleanup: None,
) -> None:
    """[E-08][RULE-01/NFR-REL-02] 真实 PG + 真实内部端点 + 每次新建 client。

    Run 1（无平台设置）用 schema 默认；随后保存 revision 1（10 秒 TTL 窗口**内**）并立刻用
    **新构造**的 service + client 建 Run 2，必须读到新 revision。若把进程级 TTL 单例加回来，
    Run 2 会在这 10 秒窗口里读到缓存旧值（= schema 默认），本用例即变红。
    """
    new_groups = 41
    async with _console_server() as console_url:
        run1 = await _start_run(tenant, console_url, fake_resolve, executor_factory)
        policy1 = await _frozen_policy(run1)
        assert policy1["compaction"]["snip"]["max_groups"] == DEFAULT_MAX_GROUPS

        await _seed_platform_setting(
            tenant.tenant_id,
            revision=1,
            document={"compaction": {"snip": {"max_groups": new_groups}}},
        )

        # 紧接保存之后（远早于旧设计的 10s TTL 到期），全新 service + 全新 client
        run2 = await _start_run(tenant, console_url, fake_resolve, executor_factory)
        policy2 = await _frozen_policy(run2)

    assert policy2["compaction"]["snip"]["max_groups"] == new_groups, (
        "保存后的新 Run 必须立即读到新 revision（无 TTL、无重启、无等待窗口）"
    )
    assert run1 != run2


# ---- 附带：每个新 Run 恰好取一次快照（不进入每轮模型调用/工具执行）----


class _CountingSettingsClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> Any:
        from muad_agent_runtime.application.ports import PlatformSettingsSnapshot

        self.calls.append(tenant_id)
        return PlatformSettingsSnapshot(revision=0, settings={})


async def test_run_creation_fetches_snapshot_exactly_once(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
) -> None:
    """[NFR-PERF-01/RULE-08] 每个新 Run 恰好取一次快照；执行期不再取。"""
    counting = _CountingSettingsClient()
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            "instance-a",
            executor_factory=executor_factory,
            settings_client=counting,
        )
        started = await service.start(_request(tenant), tenant.tenant_id)
        await _drain(started.events)

    assert len(counting.calls) == 1, "一个 Run 只应在创建边界取一次设置快照"


# ---- 附带：真实内部端点契约（路径 + 调用方头）----


async def test_settings_client_uses_internal_contract(
    tenant: TenantContext, control_cleanup: None
) -> None:
    """client 打真实内部端点：GET 路径正确、带服务身份与 `X-Caller-Service: runtime`。"""
    async with _console_server() as console_url:
        client = _settings_client(console_url)
        try:
            snapshot = await client.fetch_snapshot(tenant_id=tenant.tenant_id)
        finally:
            await client.aclose()
    assert snapshot.revision == 0  # 该租户无记录 ⇒ schema 默认
    assert PLATFORM_SETTINGS_PATH == "/internal/v1/platform-settings"
    assert "compaction" in snapshot.settings, "内部端点返回整份设置文档（含 compaction 分组）"
