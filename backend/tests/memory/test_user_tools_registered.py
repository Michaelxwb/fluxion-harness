"""TASK-011（phase2）用户自助 tool 注册 + 调用验收测试。

真实边界：真实 ToolRuntime 注册 + SQLiteRegistryStore + UserDomainService。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from fluxion.memory.application.user_tools import register_user_tools
from fluxion.observability.context import RequestContext
from fluxion.registry import SQLiteRegistryStore
from fluxion.registry.schema import audit_logs
from fluxion.resources.contracts import ExecutionSnapshot, ModelPolicy
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.tools import ToolAuthorizationError, ToolResultStatus, ToolRuntime
from fluxion.users.service import UserDomainService

TENANT = "tenant-a"
USER = "user-a"


def _real_context() -> RuntimeContext:
    """真实 RuntimeContext：emit 进 trace，供 runtime.call() 走授权 + event 链路。"""
    return RuntimeContext(
        request=RequestContext(
            request_id="req-1",
            trace_id="trace-1",
            tenant_id=TENANT,
            actor_id=USER,
            method="POST",
            route="/api/v1/chat/messages",
            client_ip="127.0.0.1",
            user_agent="test",
        ),
        snapshot=ExecutionSnapshot(
            execution_id="exec-1",
            tenant_id=TENANT,
            user_id=USER,
            runtime_profile_id="assistant",
            runtime_profile_version="1",
            model_resolution=ModelPolicy(),
            trace_id="trace-1",
        ),
    )


@pytest.fixture
async def store() -> AsyncGenerator[SQLiteRegistryStore, None]:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
async def runtime(store: SQLiteRegistryStore) -> ToolRuntime:
    rt = ToolRuntime()
    users = UserDomainService(store)
    register_user_tools(rt, engine=store.engine, users=users)
    return rt


@pytest.fixture
async def users(store: SQLiteRegistryStore) -> UserDomainService:
    svc = UserDomainService(store)
    await svc.ensure_user(tenant_id=TENANT, platform_user_id=USER, display_name="A")
    return svc


def _context(user_id: str = USER):
    return SimpleNamespace(
        snapshot=SimpleNamespace(tenant_id=TENANT, user_id=user_id, agent_definition_id="assistant"),
        tool_policy=None,
    )


@pytest.mark.asyncio
async def test_user_tools_registered(runtime: ToolRuntime) -> None:
    """8 个用户自助工具全部注册到 ToolRuntime。"""
    expected = {
        "user.profile.get", "user.profile.update",
        "user.preference.get", "user.preference.set",
        "user.memory.list", "user.memory.search",
        "user.memory.correct", "user.memory.delete",
    }
    for tool_id in expected:
        descriptor = runtime.descriptor(tool_id)
        assert descriptor is not None, f"{tool_id} not registered"
        assert descriptor.risk_level in ("low", "medium")


@pytest.mark.asyncio
async def test_s10_preference_set_then_read(
    runtime: ToolRuntime, store: SQLiteRegistryStore, users: UserDomainService
) -> None:
    """经 tool 设偏好 → user_preferences 表生效。"""
    ctx = _context()
    result = await runtime._executors["user.preference.set"](ctx, {"key": "theme", "value": "dark"})
    assert result["ok"] is True
    prefs = await users.get_preferences(tenant_id=TENANT, platform_user_id=USER)
    assert prefs is not None
    assert prefs["preference_json"]["theme"] == "dark"


@pytest.mark.asyncio
async def test_s10_profile_get_returns_data(
    runtime: ToolRuntime, store: SQLiteRegistryStore, users: UserDomainService
) -> None:
    """经 tool 读 profile → 返回 display_name。"""
    await users.upsert_profile(
        tenant_id=TENANT, platform_user_id=USER,
        spec={"display_name": "用户A", "bio": "", "timezone": "UTC", "language": "zh-CN"},
    )
    ctx = _context()
    result = await runtime._executors["user.profile.get"](ctx, {})
    assert result["ok"] is True
    assert result["data"]["profile"]["display_name"] == "用户A"


@pytest.mark.asyncio
async def test_s10_learning_disabled_blocks_memory_delete(
    runtime: ToolRuntime, store: SQLiteRegistryStore, users: UserDomainService
) -> None:
    """停学用户 → user.memory.delete 被拒。"""
    await users.set_preferences(
        tenant_id=TENANT, platform_user_id=USER, spec={"learning_enabled": False}
    )
    ctx = _context()
    result = await runtime._executors["user.memory.delete"](
        ctx, {"entry_id": 1}
    )
    assert result["ok"] is False
    assert result["error"] == "learning_disabled"


@pytest.mark.asyncio
async def test_s10_real_call_through_triple_gate_and_audit(
    runtime: ToolRuntime, store: SQLiteRegistryStore, users: UserDomainService
) -> None:
    """真实调用链：ToolRuntime.call → 三重交集 gate → executor → AuditLog。

    与既有 `_executors[...]` 私有访问不同：走公开 call 入口，验证授权 gate、
    policy_decision event 与写操作 AuditLog 全链路。
    """
    tool_id = "user.preference.set"
    ctx = _real_context()
    result = await runtime.call(
        ctx,
        tool_id,
        {"key": "theme", "value": "dark"},
        user_grants={tool_id},
        agent_allowlist={tool_id},
        tenant_policy={tool_id},
    )
    assert result.status == ToolResultStatus.COMPLETED
    assert result.result["ok"] is True
    # 偏好已落库
    prefs = await users.get_preferences(tenant_id=TENANT, platform_user_id=USER)
    assert prefs["preference_json"]["theme"] == "dark"
    # 三重交集授权通过：policy_decision event allowed=True
    decisions = [e for e in ctx.trace if e.name == "tool.policy_decision"]
    assert decisions and decisions[0].attributes["allowed"] is True
    # 写操作进 AuditLog（规则 24）
    async with store.engine.connect() as conn:
        row = (
            await conn.execute(
                select(audit_logs.c.action)
                .where(audit_logs.c.tenant_id == TENANT, audit_logs.c.action == tool_id)
            )
        ).first()
    assert row is not None, "tool 写操作必须进 AuditLog"


@pytest.mark.asyncio
async def test_s10_real_call_denied_by_gate_no_audit(
    runtime: ToolRuntime, store: SQLiteRegistryStore, users: UserDomainService
) -> None:
    """三重交集不满足 → fail-closed 拒绝，executor 不执行、不写 AuditLog。"""
    tool_id = "user.profile.get"
    ctx = _real_context()
    with pytest.raises(ToolAuthorizationError) as exc:
        await runtime.call(
            ctx,
            tool_id,
            {},
            user_grants=set(),
            agent_allowlist={tool_id},
            tenant_policy={tool_id},
        )
    assert exc.value.code == "tool_not_allowed"
    # 拒绝时 policy_decision event 记录 allowed=False（可观测）
    decisions = [e for e in ctx.trace if e.name == "tool.policy_decision"]
    assert decisions and decisions[0].attributes["allowed"] is False
    # 拒绝发生在 executor 之前：不产生该 tool 的 AuditLog
    # （fixture 的 ensure_user 会写 user.create，需按 tool 动作精确断言）
    async with store.engine.connect() as conn:
        rows = (await conn.execute(select(audit_logs.c.action))).mappings().all()
    tool_rows = [r["action"] for r in rows if r["action"] == tool_id]
    assert tool_rows == [], f"拒绝调用不应产生 {tool_id} AuditLog，got {tool_rows}"
