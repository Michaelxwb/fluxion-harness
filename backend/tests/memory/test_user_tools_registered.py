"""TASK-011（phase2）用户自助 tool 注册 + 调用验收测试。

真实边界：真实 ToolRuntime 注册 + SQLiteRegistryStore + UserDomainService。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from fluxion.memory.application.user_tools import register_user_tools
from fluxion.registry import SQLiteRegistryStore
from fluxion.registry.schema import user_preferences
from fluxion.runtime.tools import ToolRuntime
from fluxion.users.service import UserDomainService

TENANT = "tenant-a"
USER = "user-a"


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
